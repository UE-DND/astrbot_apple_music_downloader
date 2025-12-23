"""
下载核心流程。
负责完整下载与封装。
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any, Dict

from .types import Codec, M3U8Info, SongInfo, PREFETCH_KEY
from .metadata import SongMetadata
from .models import PlaylistInfo
from .mp4 import (
    extract_media, extract_song, encapsulate,
    write_metadata, fix_encapsulate, fix_esds_box,
    check_song_integrity, CodecNotFoundException
)
from .utils import (
    get_codec_from_codec_id, if_raw_atmos, run_sync,
    check_song_exists, ttml_convent
)


logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.DEBUG)
logger.setLevel(logging.DEBUG)


class DownloadStatus(Enum):
    """下载任务状态。"""
    PENDING = "pending"
    GETTING_METADATA = "getting_metadata"
    GETTING_LYRICS = "getting_lyrics"
    DOWNLOADING = "downloading"
    DECRYPTING = "decrypting"
    PROCESSING = "processing"
    SAVING = "saving"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DownloadTask:
    """下载任务容器。"""
    adam_id: str
    storefront: str
    language: str

    status: DownloadStatus = DownloadStatus.PENDING
    error_message: Optional[str] = None

    metadata: Optional[SongMetadata] = None
    m3u8_info: Optional[M3U8Info] = None
    song_info: Optional[SongInfo] = None

    decrypted_samples: list = field(default_factory=list)
    decrypted_count: int = 0
    decrypt_event: Optional[asyncio.Event] = None
    decrypt_error: Optional[str] = None

    output_path: Optional[str] = None
    actual_codec: Optional[str] = None

    def init_decrypted_samples(self):
        """初始化解密样本列表。"""
        if self.song_info:
            self.decrypted_samples = [None] * len(self.song_info.samples)
            self.decrypted_count = 0
            self.decrypt_event = asyncio.Event()
            self.decrypt_error = None

    def is_decrypt_complete(self) -> bool:
        """检查是否完成全部样本解密。"""
        return self.decrypted_count == len(self.decrypted_samples)

    def on_sample_decrypted(self, sample_index: int, sample: bytes):
        """处理已解密样本。"""
        self.decrypted_samples[sample_index] = sample
        self.decrypted_count += 1
        if self.is_decrypt_complete() and self.decrypt_event:
            self.decrypt_event.set()

    def on_decrypt_failed(self, error: str):
        """处理解密失败。"""
        self.decrypt_error = error
        if self.decrypt_event:
            self.decrypt_event.set()


@dataclass
class DownloadConfig:
    """下载配置。"""
    codec: str = Codec.ALAC
    codec_priority: list = field(default_factory=lambda: [Codec.ALAC, Codec.EC3, Codec.AC3, Codec.AAC])
    codec_alternative: bool = True
    max_bit_depth: int = 24
    max_sample_rate: int = 192000
    atmos_convert_to_m4a: bool = True
    save_lyrics: bool = True
    lyrics_format: str = "lrc"
    lyrics_extra: list = field(default_factory=lambda: ["translation", "pronunciation"])
    save_cover: bool = True
    cover_format: str = "jpg"
    cover_size: str = "5000x5000"
    embed_metadata: list = field(default_factory=lambda: [
        "title", "artist", "album", "album_artist", "composer", "album_created",
        "genre", "created", "track", "tracknum", "disk", "lyrics", "cover",
        "copyright", "record_company", "upc", "isrc", "rtng"
    ])
    force_save: bool = False
    fail_on_integrity_check: bool = False
    decrypt_timeout_seconds: float = 600.0


@dataclass
class DownloadResult:
    """下载结果。"""
    success: bool
    status: DownloadStatus
    message: str
    output_path: Optional[str] = None
    metadata: Optional[SongMetadata] = None
    codec: Optional[str] = None
    song_data: Optional[bytes] = None
    lyrics: Optional[str] = None
    cover: Optional[bytes] = None


class DecryptionManager:
    """异步解密流程管理器。"""

    def __init__(self, wrapper_manager: Any):
        self.wrapper_manager = wrapper_manager
        self._tasks: Dict[str, DownloadTask] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()
        logger.debug("[DecryptionManager] Created new instance")

    async def ensure_initialized(self):
        """确保解密流已初始化。"""
        logger.debug("[DecryptionManager] ensure_initialized called, current state: initialized=%s", self._initialized)
        async with self._init_lock:
            if not self._initialized:
                logger.info("[DecryptionManager] Initializing decrypt stream...")
                try:
                    await self.wrapper_manager.decrypt_init(
                        on_success=self._on_decrypt_success,
                        on_failure=self._on_decrypt_failure
                    )
                    self._initialized = True
                    logger.info("[DecryptionManager] Decrypt stream initialized successfully")
                except Exception as e:
                    logger.error("[DecryptionManager] Failed to initialize decrypt stream: %s", e)
                    raise
            else:
                logger.debug("[DecryptionManager] Decrypt stream already initialized")

    async def _on_decrypt_success(
        self, adam_id: str, key: str, sample: bytes, sample_index: int
    ):
        """解密成功回调。"""
        logger.debug(f"[DecryptionManager] _on_decrypt_success called: adam_id={adam_id}, sample_index={sample_index}, sample_size={len(sample) if sample else 0}")
        if adam_id in self._tasks:
            task = self._tasks[adam_id]
            task.on_sample_decrypted(sample_index, sample)
            logger.debug(f"[{adam_id}] Decrypted sample {sample_index + 1}/{len(task.decrypted_samples)}")
        else:
            logger.warning(f"[DecryptionManager] Received decrypt success for unknown task: {adam_id}")

    async def _on_decrypt_failure(
        self, adam_id: str, key: str, sample: bytes, sample_index: int
    ):
        """解密失败回调（触发重试）。"""
        logger.warning(f"[DecryptionManager] Decrypt failed for {adam_id} sample {sample_index}, retrying...")
        try:
            await self.wrapper_manager.decrypt(adam_id, key, sample, sample_index)
            logger.debug(f"[DecryptionManager] Retry queued for {adam_id} sample {sample_index}")
        except Exception as e:
            logger.error(f"[DecryptionManager] Failed to retry decrypt for {adam_id} sample {sample_index}: {e}")

    async def decrypt_song(
        self,
        task: DownloadTask,
        timeout: float = 300.0
    ) -> bool:
        """解密歌曲全部样本。"""
        logger.info(f"[DecryptionManager] decrypt_song called for {task.adam_id}, timeout={timeout}s")
        logger.debug(f"[DecryptionManager] Task has {len(task.song_info.samples)} samples to decrypt")

        await self.ensure_initialized()

        self._tasks[task.adam_id] = task
        logger.debug(f"[DecryptionManager] Task {task.adam_id} registered, total active tasks: {len(self._tasks)}")

        try:
            total_samples = len(task.song_info.samples)
            logger.info(f"[{task.adam_id}] Queuing {total_samples} samples for decryption...")

            for sample_index, sample in enumerate(task.song_info.samples):
                key, is_prefetch = resolve_decrypt_key(task.m3u8_info.keys, sample.descIndex)
                if not key:
                    logger.error(f"[{task.adam_id}] No decrypt key resolved for descIndex={sample.descIndex}")
                    task.on_decrypt_failed(f"无可用解密密钥 (descIndex={sample.descIndex})")
                    return False

                logger.debug(f"[{task.adam_id}] Queuing sample {sample_index + 1}/{total_samples}, descIndex={sample.descIndex}, data_size={len(sample.data) if sample.data else 0}")
                try:
                    await self.wrapper_manager.decrypt(
                        task.adam_id, key, sample.data, sample_index
                    )
                except Exception as e:
                    logger.error(f"[{task.adam_id}] Failed to queue sample {sample_index} for decryption: {e}")
                    task.on_decrypt_failed(f"Failed to queue sample {sample_index}: {e}")
                    return False

            logger.info(f"[{task.adam_id}] All {total_samples} samples queued, waiting for decryption completion...")

            try:
                await asyncio.wait_for(task.decrypt_event.wait(), timeout=timeout)
                logger.info(f"[{task.adam_id}] Decrypt event received, checking results...")
            except asyncio.TimeoutError:
                logger.error(f"[{task.adam_id}] Decryption timed out after {timeout}s, decrypted {task.decrypted_count}/{total_samples} samples")
                task.on_decrypt_failed("解密超时")
                return False

            if task.decrypt_error:
                logger.error(f"[{task.adam_id}] Decryption failed with error: {task.decrypt_error}")
                return False

            is_complete = task.is_decrypt_complete()
            logger.info(f"[{task.adam_id}] Decryption complete: {is_complete}, decrypted {task.decrypted_count}/{total_samples} samples")
            return is_complete

        except Exception as e:
            logger.exception(f"[{task.adam_id}] Unexpected error in decrypt_song: {e}")
            return False

        finally:
            if task.adam_id in self._tasks:
                del self._tasks[task.adam_id]
                logger.debug(f"[{task.adam_id}] Task unregistered, remaining active tasks: {len(self._tasks)}")


_decryption_managers: Dict[int, DecryptionManager] = {}


def get_decryption_manager(wrapper_manager: Any) -> DecryptionManager:
    """获取或创建 DecryptionManager。"""
    manager_id = id(wrapper_manager)
    if manager_id not in _decryption_managers:
        _decryption_managers[manager_id] = DecryptionManager(wrapper_manager)
    return _decryption_managers[manager_id]


def resolve_decrypt_key(keys: list[str], desc_index: int) -> tuple[Optional[str], bool]:
    """解析样本解密密钥。"""
    if not keys:
        return None, False

    if 0 <= desc_index < len(keys):
        key = keys[desc_index]
        return key, key == PREFETCH_KEY

    real_key = next((k for k in keys if k != PREFETCH_KEY), None)
    if real_key:
        return real_key, False

    return keys[0], True


async def rip_song(
    song_id: str,
    storefront: str,
    language: str,
    config: DownloadConfig,
    api_client: Any,
    wrapper_manager: Any,
    progress_callback: Optional[Callable[[DownloadStatus, str], None]] = None,
    check_existence: bool = True,
    plugin_config: Any = None,
    wrapper_service: Any = None,  # 可选：用于快速 decrypt_all
    playlist: Optional[PlaylistInfo] = None
) -> DownloadResult:
    """下载单曲并完成解密与封装。"""
    logger.info(f"[rip_song] ========== Starting download for song_id={song_id} ==========")
    logger.info(f"[rip_song] Parameters: storefront={storefront}, language={language}, codec={config.codec}")
    logger.debug(f"[rip_song] Config: save_lyrics={config.save_lyrics}, save_cover={config.save_cover}, force_save={config.force_save}")

    task = DownloadTask(adam_id=song_id, storefront=storefront, language=language)

    def update_status(status: DownloadStatus, message: str = ""):
        task.status = status
        if progress_callback:
            progress_callback(status, message)
        logger.info(f"[{song_id}] Status: {status.value} - {message}")

    try:
        update_status(DownloadStatus.GETTING_METADATA, "获取歌曲信息...")
        logger.info(f"[{song_id}] Step 1: Calling api_client.get_song_info...")

        raw_metadata = await api_client.get_song_info(song_id, storefront, language)
        logger.info(f"[{song_id}] Step 1: get_song_info returned: {raw_metadata is not None}")
        if not raw_metadata:
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                message="无法获取歌曲信息"
            )

        album_id = None
        if raw_metadata.relationships.albums.data:
            album_id = raw_metadata.relationships.albums.data[0].id
            logger.info(f"[{song_id}] Step 1b: Getting album info for album_id={album_id}")
            album_data = await api_client.get_album_info(album_id, storefront, language)
            logger.info(f"[{song_id}] Step 1b: get_album_info returned: {album_data is not None}")
        else:
            album_data = None
            logger.info(f"[{song_id}] Step 1b: No album info available")

        logger.debug(f"[{song_id}] Step 1c: Parsing metadata from song data...")
        task.metadata = SongMetadata.parse_from_song_data(raw_metadata)
        if album_data:
            task.metadata.parse_from_album_data(album_data)
        logger.info(f"[{song_id}] Step 1c: Metadata parsed - title={task.metadata.title}, artist={task.metadata.artist}")

        if playlist and task.metadata:
            task.metadata.set_playlist_index(playlist.songIdIndexMapping.get(song_id))

        logger.info(f"[{song_id}] Step 3: Checking file existence (check_existence={check_existence}, force_save={config.force_save})")
        if check_existence and plugin_config and not config.force_save:
            if check_song_exists(task.metadata, config.codec, plugin_config, playlist):
                logger.info(f"[{song_id}] Step 3: File already exists, skipping download")
                return DownloadResult(
                    success=True,
                    status=DownloadStatus.SKIPPED,
                    message="文件已存在",
                    metadata=task.metadata
                )

        update_status(DownloadStatus.GETTING_LYRICS, "获取歌词...")
        logger.info(f"[{song_id}] Step 4: Getting lyrics (save_lyrics={config.save_lyrics}, hasTimeSyncedLyrics={raw_metadata.attributes.hasTimeSyncedLyrics})")

        if config.save_lyrics and raw_metadata.attributes.hasTimeSyncedLyrics:
            try:
                logger.debug(f"[{song_id}] Step 4: Calling wrapper_manager.lyrics...")
                ttml_lyrics = await wrapper_manager.lyrics(song_id, language, storefront)
                logger.info(f"[{song_id}] Step 4: wrapper_manager.lyrics returned: {ttml_lyrics is not None}, len={len(ttml_lyrics) if ttml_lyrics else 0}")
                if ttml_lyrics:
                    lrc_lyrics = ttml_convent(
                        ttml_lyrics,
                        config.lyrics_format,
                        config.lyrics_extra
                    )
                    task.metadata.set_lyrics(lrc_lyrics)
                    logger.info(f"[{song_id}] Step 4: Lyrics converted and set")
            except Exception as e:
                logger.warning(f"[{song_id}] Step 4: Failed to get lyrics: {e}")
        else:
            logger.info(f"[{song_id}] Step 4: Skipping lyrics (not configured or not available)")

        logger.info(f"[{song_id}] Step 4: Getting cover (save_cover={config.save_cover}, cover_url={task.metadata.cover_url is not None})")
        if config.save_cover and task.metadata.cover_url:
            try:
                logger.debug(f"[{song_id}] Step 4: Calling api_client.get_cover...")
                cover_data = await api_client.get_cover(
                    task.metadata.cover_url,
                    config.cover_format,
                    config.cover_size
                )
                task.metadata.set_cover(cover_data)
                logger.info(f"[{song_id}] Step 4: Cover downloaded, size={len(cover_data) if cover_data else 0} bytes")
            except Exception as e:
                logger.warning(f"[{song_id}] Step 4: Failed to get cover: {e}")
        else:
            logger.info(f"[{song_id}] Step 4: Skipping cover")

        update_status(DownloadStatus.DOWNLOADING, "下载中...")
        logger.info(f"[{song_id}] Step 5: Getting M3U8 and downloading audio...")

        has_extended_urls = raw_metadata.attributes.extendedAssetUrls is not None
        logger.debug(f"[{song_id}] Step 5: extendedAssetUrls available: {has_extended_urls}")
        if not raw_metadata.attributes.extendedAssetUrls:
            logger.error(f"[{song_id}] Step 5: No extended asset URLs available")
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                message="该歌曲没有可用的音频资源"
            )

        codec = config.codec
        logger.info(f"[{song_id}] Step 5a: Requested codec={codec}")
        if codec == Codec.ALAC and raw_metadata.attributes.extendedAssetUrls.enhancedHls:
            logger.debug(f"[{song_id}] Step 5a: Calling wrapper_manager.m3u8 for ALAC...")
            m3u8_url = await wrapper_manager.m3u8(song_id)
            logger.info(f"[{song_id}] Step 5a: wrapper_manager.m3u8 returned: {m3u8_url is not None}")
        else:
            m3u8_url = raw_metadata.attributes.extendedAssetUrls.enhancedHls
            logger.info(f"[{song_id}] Step 5a: Using enhancedHls URL directly")

        if not m3u8_url:
            logger.error(f"[{song_id}] Step 5a: No M3U8 URL available")
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                message="无法获取音频 M3U8 地址"
            )

        logger.info(f"[{song_id}] Step 5b: Extracting media info from M3U8...")
        try:
            logger.debug(f"[{song_id}] Step 5b: Downloading M3U8 content...")
            m3u8_content = await api_client.download_m3u8(m3u8_url)
            logger.debug(f"[{song_id}] Step 5b: M3U8 content length={len(m3u8_content) if m3u8_content else 0}")

            logger.debug(f"[{song_id}] Step 5b: Calling extract_media...")
            task.m3u8_info, task.actual_codec = await extract_media(
                m3u8_content,
                m3u8_url,
                codec,
                config.codec_priority,
                config.codec_alternative,
                config.max_bit_depth,
                config.max_sample_rate,
                api_client.download_m3u8
            )
            logger.info(f"[{song_id}] Step 5b: extract_media completed, actual_codec={task.actual_codec}")
        except CodecNotFoundException:
            logger.error(f"[{song_id}] Step 5b: Codec {codec} not found")
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                message=f"请求的编码格式 {codec} 不可用"
            )

        if task.m3u8_info.bit_depth and task.m3u8_info.sample_rate:
            logger.info(f"[{song_id}] Step 5c: Audio info - bit_depth={task.m3u8_info.bit_depth}, sample_rate={task.m3u8_info.sample_rate}")
            task.metadata.set_bit_depth_and_sample_rate(
                task.m3u8_info.bit_depth,
                task.m3u8_info.sample_rate
            )

        logger.info(f"[{song_id}] Step 5d: Downloading raw audio from URI...")
        raw_song = await api_client.download_song(task.m3u8_info.uri)
        logger.info(f"[{song_id}] Step 5d: Raw audio downloaded, size={len(raw_song) if raw_song else 0} bytes")

        update_status(DownloadStatus.DECRYPTING, "解密中...")
        logger.info(f"[{song_id}] Step 6: Starting decryption...")

        actual_codec = get_codec_from_codec_id(task.m3u8_info.codec_id)
        logger.info(f"[{song_id}] Step 6a: Actual codec from codec_id: {actual_codec}")

        logger.debug(f"[{song_id}] Step 6b: Extracting song info from raw data...")
        task.song_info = await run_sync(extract_song, raw_song, actual_codec)
        logger.info(f"[{song_id}] Step 6b: Song info extracted, samples count={len(task.song_info.samples) if task.song_info else 0}")

        task.init_decrypted_samples()
        logger.debug(f"[{song_id}] Step 6c: Initialized decrypted_samples array")

        decrypt_success = False

        # 优先使用原生模式的快速 decrypt_all，避免逐样本连接带来的性能瓶颈
        enable_fast_decrypt = False
        if wrapper_service and hasattr(wrapper_service, "decrypt_all"):
            mode_value = getattr(getattr(wrapper_service, "mode", None), "value", None)
            if mode_value is None:
                mode_value = getattr(wrapper_service, "mode", None)
            enable_fast_decrypt = mode_value in (None, "native")
        if enable_fast_decrypt:
            logger.info(f"[{song_id}] Step 6d: Using FAST decrypt_all with per-key grouping...")

            total_samples = len(task.song_info.samples)
            task.decrypted_samples = [None] * total_samples

            from collections import Counter, defaultdict

            grouped_samples = defaultdict(list)
            decrypt_counter = 0
            fast_error: Optional[str] = None

            # 按密钥分组，复用同一条连接
            for idx, sample in enumerate(task.song_info.samples):
                key, is_prefetch = resolve_decrypt_key(task.m3u8_info.keys, sample.descIndex)
                if not key:
                    fast_error = f"descIndex={sample.descIndex} 无可用解密密钥"
                    logger.error(f"[{song_id}] Step 6d: {fast_error}")
                    break

                grouped_samples[key].append((sample.data, idx))

            if fast_error is None:
                decrypt_success = True
                for key, samples_for_key in grouped_samples.items():
                    decrypt_success, decrypted_list, error_msg = await wrapper_service.decrypt_all(
                        song_id, key, samples_for_key, None
                    )

                    if not decrypt_success or not decrypted_list or len(decrypted_list) != len(samples_for_key):
                        fast_error = error_msg or "解密失败"
                        decrypt_success = False
                        logger.error(f"[{song_id}] Step 6d: Fast decrypt failed for key={key}: {fast_error}")
                        break

                    for decrypted_sample, (_, original_index) in zip(decrypted_list, samples_for_key):
                        task.decrypted_samples[original_index] = decrypted_sample

                    decrypt_counter += len(samples_for_key)

                task.decrypted_count = decrypt_counter
                decrypt_success = decrypt_success and None not in task.decrypted_samples

            if not decrypt_success:
                logger.warning(f"[{song_id}] Fast decrypt_all unavailable or failed, fallback to stream decrypt. Reason: {fast_error or 'unknown'}")

        if not decrypt_success:
            # 远程模式或快速解密失败时回退到流式解密
            logger.info(f"[{song_id}] Step 6d: Using slow DecryptionManager (gRPC stream)...")
            decrypt_manager = get_decryption_manager(wrapper_manager)
            logger.info(f"[{song_id}] Step 6e: Calling decrypt_manager.decrypt_song...")
            task.init_decrypted_samples()
            decrypt_success = await decrypt_manager.decrypt_song(
                task,
                timeout=config.decrypt_timeout_seconds
            )
            logger.info(f"[{song_id}] Step 6e: decrypt_song returned: success={decrypt_success}")

            if not decrypt_success:
                error_msg = task.decrypt_error or "解密失败"
                logger.error(f"[{song_id}] Step 6: Decryption failed: {error_msg}")
                return DownloadResult(
                    success=False,
                    status=DownloadStatus.FAILED,
                    message=error_msg
                )

        update_status(DownloadStatus.PROCESSING, "处理中...")
        logger.info(f"[{song_id}] Step 7: Processing and encapsulating...")

        logger.debug(f"[{song_id}] Step 7a: Joining decrypted samples...")
        decrypted_media = bytes().join(task.decrypted_samples)
        logger.info(f"[{song_id}] Step 7a: Total decrypted media size={len(decrypted_media)} bytes")

        logger.debug(f"[{song_id}] Step 7b: Encapsulating...")
        song = await run_sync(encapsulate, task.song_info, decrypted_media, config.atmos_convert_to_m4a)
        logger.info(f"[{song_id}] Step 7b: Encapsulation complete, size={len(song) if song else 0} bytes")

        is_raw_atmos = if_raw_atmos(actual_codec, config.atmos_convert_to_m4a)
        logger.info(f"[{song_id}] Step 7c: Post-processing (is_raw_atmos={is_raw_atmos}, actual_codec={actual_codec})")

        if not is_raw_atmos:
            # ALAC 跳过 fix_encapsulate，避免 FFmpeg 移除 ALAC 配置导致帧信息异常
            if actual_codec not in [Codec.EC3, Codec.AC3, Codec.ALAC]:
                logger.debug(f"[{song_id}] Step 7c: Fixing encapsulation...")
                song = await run_sync(fix_encapsulate, song)
            else:
                logger.debug(f"[{song_id}] Step 7c: Skipping fix_encapsulate for codec={actual_codec}")

            # ALAC 暂时跳过写入元数据，避免封装改写导致帧信息损坏
            if actual_codec != Codec.ALAC:
                logger.debug(f"[{song_id}] Step 7d: Writing metadata...")
                song = await run_sync(
                    write_metadata,
                    song,
                    task.metadata,
                    config.embed_metadata,
                    config.cover_format,
                    task.song_info.params
                )
                logger.info(f"[{song_id}] Step 7d: Metadata written")

            # AAC 需要修复 ESDS box
            if actual_codec in [Codec.AAC, Codec.AAC_DOWNMIX, Codec.AAC_BINAURAL]:
                logger.debug(f"[{song_id}] Step 7e: Fixing ESDS box for AAC codec...")
                song = await run_sync(fix_esds_box, task.song_info.raw, song)
                logger.info(f"[{song_id}] Step 7e: ESDS box fixed")

        logger.info(f"[{song_id}] Step 8: Checking file integrity...")
        integrity_ok = await run_sync(check_song_integrity, song)
        logger.info(f"[{song_id}] Step 8: Integrity check result: {integrity_ok}")

        if not integrity_ok:
            if config.fail_on_integrity_check:
                logger.error(f"[{song_id}] Step 8: Integrity check failed and fail_on_integrity_check=True")
                return DownloadResult(
                    success=False,
                    status=DownloadStatus.FAILED,
                    message="文件完整性检查失败"
                )
            else:
                logger.warning(f"[{song_id}] Step 8: File integrity check failed, but continuing")

        update_status(DownloadStatus.DONE, "完成")
        logger.info(f"[{song_id}] ========== Download completed successfully ==========")
        logger.info(f"[{song_id}] Final song size={len(song) if song else 0} bytes, codec={actual_codec}")

        return DownloadResult(
            success=True,
            status=DownloadStatus.DONE,
            message="下载完成",
            metadata=task.metadata,
            codec=actual_codec,
            song_data=song,
            lyrics=task.metadata.lyrics if config.save_lyrics else None,
            cover=task.metadata.cover if config.save_cover else None
        )

    except Exception as e:
        logger.exception(f"[{song_id}] Download failed: {e}")
        return DownloadResult(
            success=False,
            status=DownloadStatus.FAILED,
            message=f"下载失败: {str(e)}"
        )


async def get_song_info(
    song_id: str,
    storefront: str,
    language: str,
    api_client: Any
) -> Optional[dict]:
    """
    Get basic song information without downloading.

    Args:
        song_id: Apple Music song ID
        storefront: Region/storefront code
        language: Language code
        api_client: WebAPI instance

    Returns:
        Dictionary with song info or None
    """
    try:
        raw_metadata = await api_client.get_song_info(song_id, storefront, language)
        if not raw_metadata:
            return None

        return {
            "id": song_id,
            "title": raw_metadata.attributes.name,
            "artist": raw_metadata.attributes.artistName,
            "album": raw_metadata.attributes.albumName,
            "duration": raw_metadata.attributes.durationInMillis,
            "has_lyrics": raw_metadata.attributes.hasTimeSyncedLyrics,
            "has_lossless": bool(raw_metadata.attributes.extendedAssetUrls),
            "artwork_url": raw_metadata.attributes.artwork.url if raw_metadata.attributes.artwork else None,
            "release_date": raw_metadata.attributes.releaseDate,
            "isrc": raw_metadata.attributes.isrc,
        }
    except Exception as e:
        logger.exception(f"Failed to get song info: {e}")
        return None
