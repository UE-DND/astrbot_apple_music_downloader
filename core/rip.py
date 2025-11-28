"""
Download Core Logic (Rip)


Handles the complete song download workflow:
getMetadata -> getLyrics -> getM3U8 -> downloadSong -> decrypt -> encapsulate -> save
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any, Dict

from .types import Codec, M3U8Info, SongInfo
from .metadata import SongMetadata
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

# Enable debug logging for troubleshooting
logging.basicConfig(level=logging.DEBUG)
logger.setLevel(logging.DEBUG)


class DownloadStatus(Enum):
    """Download task status."""
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
    """
    Download task container.

    Holds all state and data for a single song download.
    """
    adam_id: str
    storefront: str
    language: str

    # Status
    status: DownloadStatus = DownloadStatus.PENDING
    error_message: Optional[str] = None

    # Metadata
    metadata: Optional[SongMetadata] = None
    m3u8_info: Optional[M3U8Info] = None
    song_info: Optional[SongInfo] = None

    # Decryption state
    decrypted_samples: list = field(default_factory=list)
    decrypted_count: int = 0
    decrypt_event: Optional[asyncio.Event] = None
    decrypt_error: Optional[str] = None

    # Result
    output_path: Optional[str] = None
    actual_codec: Optional[str] = None

    def init_decrypted_samples(self):
        """Initialize the decrypted samples list."""
        if self.song_info:
            self.decrypted_samples = [None] * len(self.song_info.samples)
            self.decrypted_count = 0
            self.decrypt_event = asyncio.Event()
            self.decrypt_error = None

    def is_decrypt_complete(self) -> bool:
        """Check if all samples have been decrypted."""
        return self.decrypted_count == len(self.decrypted_samples)

    def on_sample_decrypted(self, sample_index: int, sample: bytes):
        """Handle a decrypted sample."""
        self.decrypted_samples[sample_index] = sample
        self.decrypted_count += 1
        if self.is_decrypt_complete() and self.decrypt_event:
            self.decrypt_event.set()

    def on_decrypt_failed(self, error: str):
        """Handle decryption failure."""
        self.decrypt_error = error
        if self.decrypt_event:
            self.decrypt_event.set()


@dataclass
class DownloadConfig:
    """Configuration for download operations."""
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


@dataclass
class DownloadResult:
    """Result of a download operation."""
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
    """
    Manages async decryption workflow.

    Handles the gRPC streaming decryption by:
    1. Initializing the decrypt stream with callbacks
    2. Queuing samples for decryption
    3. Collecting decrypted results via callbacks
    4. Signaling completion when all samples are done
    """

    def __init__(self, wrapper_manager: Any):
        self.wrapper_manager = wrapper_manager
        self._tasks: Dict[str, DownloadTask] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()
        logger.debug("[DecryptionManager] Created new instance")

    async def ensure_initialized(self):
        """Ensure the decrypt stream is initialized."""
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
        """Callback for successful decryption."""
        logger.debug(f"[DecryptionManager] _on_decrypt_success called: adam_id={adam_id}, sample_index={sample_index}, sample_size={len(sample) if sample else 0}")
        if adam_id in self._tasks:
            task = self._tasks[adam_id]
            task.on_sample_decrypted(sample_index, sample)
            logger.info(f"[{adam_id}] Decrypted sample {sample_index + 1}/{len(task.decrypted_samples)}")
        else:
            logger.warning(f"[DecryptionManager] Received decrypt success for unknown task: {adam_id}")

    async def _on_decrypt_failure(
        self, adam_id: str, key: str, sample: bytes, sample_index: int
    ):
        """Callback for failed decryption - retry."""
        logger.warning(f"[DecryptionManager] Decrypt failed for {adam_id} sample {sample_index}, retrying...")
        # Retry by re-queuing
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
        """
        Decrypt all samples for a song.

        Args:
            task: DownloadTask with song_info and m3u8_info populated
            timeout: Maximum time to wait for decryption (seconds)

        Returns:
            True if all samples decrypted successfully, False otherwise
        """
        logger.info(f"[DecryptionManager] decrypt_song called for {task.adam_id}, timeout={timeout}s")
        logger.debug(f"[DecryptionManager] Task has {len(task.song_info.samples)} samples to decrypt")

        await self.ensure_initialized()

        # Register task
        self._tasks[task.adam_id] = task
        logger.debug(f"[DecryptionManager] Task {task.adam_id} registered, total active tasks: {len(self._tasks)}")

        try:
            # Queue all samples for decryption
            total_samples = len(task.song_info.samples)
            logger.info(f"[{task.adam_id}] Queuing {total_samples} samples for decryption...")

            for sample_index, sample in enumerate(task.song_info.samples):
                key = task.m3u8_info.keys[sample.descIndex]
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

            # Wait for all samples to be decrypted
            try:
                await asyncio.wait_for(task.decrypt_event.wait(), timeout=timeout)
                logger.info(f"[{task.adam_id}] Decrypt event received, checking results...")
            except asyncio.TimeoutError:
                logger.error(f"[{task.adam_id}] Decryption timed out after {timeout}s, decrypted {task.decrypted_count}/{total_samples} samples")
                task.on_decrypt_failed("解密超时")
                return False

            # Check for errors
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
            # Unregister task
            if task.adam_id in self._tasks:
                del self._tasks[task.adam_id]
                logger.debug(f"[{task.adam_id}] Task unregistered, remaining active tasks: {len(self._tasks)}")


# Global decryption manager instance (per wrapper_manager)
_decryption_managers: Dict[int, DecryptionManager] = {}


def get_decryption_manager(wrapper_manager: Any) -> DecryptionManager:
    """Get or create a DecryptionManager for the given wrapper_manager."""
    manager_id = id(wrapper_manager)
    if manager_id not in _decryption_managers:
        _decryption_managers[manager_id] = DecryptionManager(wrapper_manager)
    return _decryption_managers[manager_id]


async def rip_song(
    song_id: str,
    storefront: str,
    language: str,
    config: DownloadConfig,
    api_client: Any,
    wrapper_manager: Any,
    progress_callback: Optional[Callable[[DownloadStatus, str], None]] = None,
    check_existence: bool = True,
    plugin_config: Any = None
) -> DownloadResult:
    """
    Download a single song.

    This is the main entry point for downloading songs. It handles:
    1. Getting metadata from Apple Music API
    2. Getting lyrics if available
    3. Getting M3U8 playlist and selecting codec
    4. Downloading encrypted audio
    5. Decrypting samples via wrapper service (async callback mode)
    6. Encapsulating into final format
    7. Writing metadata

    Args:
        song_id: Apple Music song ID
        storefront: Region/storefront code
        language: Language code
        config: Download configuration
        api_client: WebAPI instance
        wrapper_manager: WrapperManager instance
        progress_callback: Optional callback for progress updates
        check_existence: Whether to check if file already exists
        plugin_config: Plugin configuration for path settings

    Returns:
        DownloadResult with success status and data
    """
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
        # Step 1: Get song metadata
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

        # Get album info for complete metadata
        album_id = None
        if raw_metadata.relationships.albums.data:
            album_id = raw_metadata.relationships.albums.data[0].id
            logger.info(f"[{song_id}] Step 1b: Getting album info for album_id={album_id}")
            album_data = await api_client.get_album_info(album_id, storefront, language)
            logger.info(f"[{song_id}] Step 1b: get_album_info returned: {album_data is not None}")
        else:
            album_data = None
            logger.info(f"[{song_id}] Step 1b: No album info available")

        # Parse metadata
        logger.debug(f"[{song_id}] Step 1c: Parsing metadata from song data...")
        task.metadata = SongMetadata.parse_from_song_data(raw_metadata)
        if album_data:
            task.metadata.parse_from_album_data(album_data)
        logger.info(f"[{song_id}] Step 1c: Metadata parsed - title={task.metadata.title}, artist={task.metadata.artist}")

        # Step 2: Check if file already exists
        logger.info(f"[{song_id}] Step 2: Checking file existence (check_existence={check_existence}, force_save={config.force_save})")
        if check_existence and plugin_config and not config.force_save:
            if check_song_exists(task.metadata, config.codec, plugin_config):
                logger.info(f"[{song_id}] Step 2: File already exists, skipping download")
                return DownloadResult(
                    success=True,
                    status=DownloadStatus.SKIPPED,
                    message="文件已存在",
                    metadata=task.metadata
                )

        # Step 3: Get lyrics
        update_status(DownloadStatus.GETTING_LYRICS, "获取歌词...")
        logger.info(f"[{song_id}] Step 3: Getting lyrics (save_lyrics={config.save_lyrics}, hasTimeSyncedLyrics={raw_metadata.attributes.hasTimeSyncedLyrics})")

        if config.save_lyrics and raw_metadata.attributes.hasTimeSyncedLyrics:
            try:
                logger.debug(f"[{song_id}] Step 3: Calling wrapper_manager.lyrics...")
                ttml_lyrics = await wrapper_manager.lyrics(song_id, language, storefront)
                logger.info(f"[{song_id}] Step 3: wrapper_manager.lyrics returned: {ttml_lyrics is not None}, len={len(ttml_lyrics) if ttml_lyrics else 0}")
                if ttml_lyrics:
                    lrc_lyrics = ttml_convent(
                        ttml_lyrics,
                        config.lyrics_format,
                        config.lyrics_extra
                    )
                    task.metadata.set_lyrics(lrc_lyrics)
                    logger.info(f"[{song_id}] Step 3: Lyrics converted and set")
            except Exception as e:
                logger.warning(f"[{song_id}] Step 3: Failed to get lyrics: {e}")
        else:
            logger.info(f"[{song_id}] Step 3: Skipping lyrics (not configured or not available)")

        # Step 4: Get cover
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

        # Step 5: Get M3U8 and download
        update_status(DownloadStatus.DOWNLOADING, "下载中...")
        logger.info(f"[{song_id}] Step 5: Getting M3U8 and downloading audio...")

        # Check if extended asset URLs are available
        has_extended_urls = raw_metadata.attributes.extendedAssetUrls is not None
        logger.debug(f"[{song_id}] Step 5: extendedAssetUrls available: {has_extended_urls}")
        if not raw_metadata.attributes.extendedAssetUrls:
            logger.error(f"[{song_id}] Step 5: No extended asset URLs available")
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                message="该歌曲没有可用的音频资源"
            )

        # Get M3U8 URL
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

        # Extract media info
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

        # Update metadata with audio info
        if task.m3u8_info.bit_depth and task.m3u8_info.sample_rate:
            logger.info(f"[{song_id}] Step 5c: Audio info - bit_depth={task.m3u8_info.bit_depth}, sample_rate={task.m3u8_info.sample_rate}")
            task.metadata.set_bit_depth_and_sample_rate(
                task.m3u8_info.bit_depth,
                task.m3u8_info.sample_rate
            )

        # Download raw audio
        logger.info(f"[{song_id}] Step 5d: Downloading raw audio from URI...")
        raw_song = await api_client.download_song(task.m3u8_info.uri)
        logger.info(f"[{song_id}] Step 5d: Raw audio downloaded, size={len(raw_song) if raw_song else 0} bytes")

        # Step 6: Decrypt using async callback mode
        update_status(DownloadStatus.DECRYPTING, "解密中...")
        logger.info(f"[{song_id}] Step 6: Starting decryption...")

        actual_codec = get_codec_from_codec_id(task.m3u8_info.codec_id)
        logger.info(f"[{song_id}] Step 6a: Actual codec from codec_id: {actual_codec}")

        logger.debug(f"[{song_id}] Step 6b: Extracting song info from raw data...")
        task.song_info = await run_sync(extract_song, raw_song, actual_codec)
        logger.info(f"[{song_id}] Step 6b: Song info extracted, samples count={len(task.song_info.samples) if task.song_info else 0}")

        task.init_decrypted_samples()
        logger.debug(f"[{song_id}] Step 6c: Initialized decrypted_samples array")

        # Use DecryptionManager for async decryption
        logger.info(f"[{song_id}] Step 6d: Getting DecryptionManager...")
        decrypt_manager = get_decryption_manager(wrapper_manager)
        logger.info(f"[{song_id}] Step 6e: Calling decrypt_manager.decrypt_song...")
        decrypt_success = await decrypt_manager.decrypt_song(task, timeout=300.0)
        logger.info(f"[{song_id}] Step 6e: decrypt_song returned: success={decrypt_success}")

        if not decrypt_success:
            error_msg = task.decrypt_error or "解密失败"
            logger.error(f"[{song_id}] Step 6: Decryption failed: {error_msg}")
            return DownloadResult(
                success=False,
                status=DownloadStatus.FAILED,
                message=error_msg
            )

        # Step 7: Process and encapsulate
        update_status(DownloadStatus.PROCESSING, "处理中...")
        logger.info(f"[{song_id}] Step 7: Processing and encapsulating...")

        logger.debug(f"[{song_id}] Step 7a: Joining decrypted samples...")
        decrypted_media = bytes().join(task.decrypted_samples)
        logger.info(f"[{song_id}] Step 7a: Total decrypted media size={len(decrypted_media)} bytes")

        logger.debug(f"[{song_id}] Step 7b: Encapsulating...")
        song = await run_sync(encapsulate, task.song_info, decrypted_media, config.atmos_convert_to_m4a)
        logger.info(f"[{song_id}] Step 7b: Encapsulation complete, size={len(song) if song else 0} bytes")

        # Post-processing for non-raw Atmos
        is_raw_atmos = if_raw_atmos(actual_codec, config.atmos_convert_to_m4a)
        logger.info(f"[{song_id}] Step 7c: Post-processing (is_raw_atmos={is_raw_atmos}, actual_codec={actual_codec})")

        if not is_raw_atmos:
            if actual_codec not in [Codec.EC3, Codec.AC3]:
                logger.debug(f"[{song_id}] Step 7c: Fixing encapsulation...")
                song = await run_sync(fix_encapsulate, song)

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

            # Fix ESDS box for AAC codecs
            if actual_codec in [Codec.AAC, Codec.AAC_DOWNMIX, Codec.AAC_BINAURAL]:
                logger.debug(f"[{song_id}] Step 7e: Fixing ESDS box for AAC codec...")
                song = await run_sync(fix_esds_box, task.song_info.raw, song)
                logger.info(f"[{song_id}] Step 7e: ESDS box fixed")

        # Integrity check
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

        # Success
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
