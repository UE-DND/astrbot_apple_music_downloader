"""
Apple Music Downloader 服务。
基于核心模块提供高层下载能力。
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from .logger import LoggerInterface, get_logger

logging.basicConfig(level=logging.DEBUG)

# 尝试相对导入,失败则使用绝对导入(支持独立运行)
try:
    from ..core import (
        WebAPI,
        AppleMusicURL,
        Song,
        Album,
        Playlist,
        Artist,
        URLType,
        PluginConfig,
        SongMetadata,
        Codec,
        rip_song,
        get_song_info,
        DownloadStatus,
        DownloadResult as CoreDownloadResult,
        RipDownloadConfig,
        save_all,
        get_output_path,
    )
except ImportError:
    # 独立运行模式
    from core import (
        WebAPI,
        AppleMusicURL,
        Song,
        Album,
        Playlist,
        Artist,
        URLType,
        PluginConfig,
        SongMetadata,
        Codec,
        rip_song,
        get_song_info,
        DownloadStatus,
        DownloadResult as CoreDownloadResult,
        RipDownloadConfig,
        save_all,
        get_output_path,
    )
from .wrapper_service import WrapperService


class DownloadQuality(Enum):
    """下载音质选项。"""
    ALAC = "alac"                 # 无损
    EC3 = "ec3"                   # Dolby Atmos（EC3）
    AC3 = "ac3"                   # Dolby Digital（AC3）
    AAC = "aac"                   # 高品质 AAC
    AAC_BINAURAL = "aac-binaural" # AAC 双声道
    AAC_DOWNMIX = "aac-downmix"   # AAC 混缩
    AAC_LEGACY = "aac-legacy"     # AAC 旧版


QUALITY_TO_CODEC = {
    DownloadQuality.ALAC: Codec.ALAC,
    DownloadQuality.EC3: Codec.EC3,
    DownloadQuality.AC3: Codec.AC3,
    DownloadQuality.AAC: Codec.AAC,
    DownloadQuality.AAC_BINAURAL: Codec.AAC_BINAURAL,
    DownloadQuality.AAC_DOWNMIX: Codec.AAC_DOWNMIX,
    DownloadQuality.AAC_LEGACY: Codec.AAC_LEGACY,
}


@dataclass
class DownloadResult:
    """下载结果。"""
    success: bool
    message: str
    file_paths: List[str] = field(default_factory=list)
    cover_path: Optional[str] = None
    lyrics_path: Optional[str] = None
    track_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    codec: Optional[str] = None


@dataclass
class ServiceStatus:
    """服务状态。"""
    wrapper_connected: bool = False
    wrapper_url: str = ""
    wrapper_regions: List[str] = field(default_factory=list)
    api_available: bool = False
    error: Optional[str] = None


class URLParser:
    """用于 Apple Music 的链接解析器。"""

    @classmethod
    def parse(cls, url: str) -> Optional[Dict[str, str]]:
        """解析 Apple Music URL。"""
        parsed = AppleMusicURL.parse_url(url.strip())
        if not parsed:
            return None

        result = {
            "type": parsed.type,
            "storefront": parsed.storefront,
            "id": parsed.id,
            "url": parsed.url,
        }

        return result

    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        """检查 URL 是否有效。"""
        return AppleMusicURL.is_valid_url(url.strip())

    @classmethod
    def get_type_display(cls, url_type: str) -> str:
        """获取 URL 类型显示名。"""
        type_names = {
            "song": "单曲",
            "album": "专辑",
            "playlist": "播放列表",
            "artist": "艺术家",
        }
        return type_names.get(url_type, url_type)


class MetadataFetcher:
    """歌曲元数据获取器。"""

    def __init__(self, api_client: WebAPI):
        self.api = api_client

    async def get_song_info(
        self,
        song_id: str,
        storefront: str = "cn",
        language: str = "zh-Hans-CN"
    ) -> Optional[str]:
        """获取歌曲信息文本（标题 - 艺术家）。"""
        try:
            info = await get_song_info(song_id, storefront, language, self.api)
            if info:
                return f"{info['title']} - {info['artist']}"
        except Exception as e:
            self.logger.warning(f"Failed to fetch song info: {e}")

        return None


class DownloaderService:
    """用于 Apple Music 的下载服务。"""

    def __init__(
        self,
        config: PluginConfig,
        wrapper_service: WrapperService,
        api_client: Optional[WebAPI] = None,
        logger: Optional[LoggerInterface] = None
    ):
        """初始化下载服务。"""
        self.config = config
        self.wrapper_service = wrapper_service
        self._api: Optional[WebAPI] = api_client
        self._metadata_fetcher: Optional[MetadataFetcher] = None
        self.logger = logger or get_logger()

        # 下载缓存
        self._cache: Dict[str, DownloadResult] = {}
        self._cache_ttl = 7 * 24 * 3600  # 7 天

    async def init(self) -> Tuple[bool, str]:
        """初始化下载服务并连接 Wrapper。"""
        try:
            if not self._api:
                self._api = WebAPI(
                    parallel_num=1,
                    cdn_ip=""
                )

            self._metadata_fetcher = MetadataFetcher(self._api)

            success, msg = await self.wrapper_service.init()
            if not success:
                return False, f"Wrapper 服务初始化失败: {msg}"

            return True, "服务初始化成功"

        except Exception as e:
            self.logger.error(f"Failed to initialize downloader service: {e}")
            return False, f"初始化失败: {str(e)}"

    async def close(self):
        """关闭下载服务。"""
        if self._api:
            await self._api.close()
        await self.wrapper_service.close()

    async def get_status(self) -> ServiceStatus:
        """获取服务状态。"""
        status = ServiceStatus()

        wrapper_status = await self.wrapper_service.get_status()
        status.wrapper_connected = wrapper_status.connected
        status.wrapper_url = wrapper_status.url
        status.wrapper_regions = wrapper_status.regions
        status.error = wrapper_status.error

        status.api_available = self._api is not None

        return status

    async def download(
        self,
        url: str,
        quality: DownloadQuality = DownloadQuality.ALAC,
        force: bool = False,
        progress_callback: Optional[callable] = None,
        playlist: Optional[Any] = None
    ) -> DownloadResult:
        """下载 Apple Music 单曲。"""
        parsed = URLParser.parse(url)
        if not parsed:
            return DownloadResult(
                success=False,
                message="无效的 Apple Music URL",
                error="URL 解析失败"
            )

        cache_key = f"{url}|{quality.value}"
        if not force and cache_key in self._cache:
            cached = self._cache[cache_key]
            if cached.success and all(Path(p).exists() for p in cached.file_paths):
                self.logger.info(f"Using cached download result for {url}")
                return cached

        if parsed["type"] != URLType.Song:
            return DownloadResult(
                success=False,
                message=f"暂不支持下载 {URLParser.get_type_display(parsed['type'])}",
                error="仅支持单曲下载"
            )

        manager = await self.wrapper_service.get_manager()
        if not manager:
            return DownloadResult(
                success=False,
                message="Wrapper 服务未连接",
                error="请先启动 Wrapper 服务"
            )

        codec = QUALITY_TO_CODEC.get(quality, Codec.ALAC)
        rip_config = RipDownloadConfig(
            codec=codec,
            codec_priority=self.config.download.codec_priority,
            codec_alternative=self.config.download.codec_alternative,
            max_bit_depth=self.config.download.max_bit_depth,
            max_sample_rate=self.config.download.max_sample_rate,
            atmos_convert_to_m4a=self.config.download.atmos_convert_to_m4a,
            save_lyrics=self.config.download.save_lyrics,
            lyrics_format=self.config.download.lyrics_format,
            lyrics_extra=self.config.download.lyrics_extra,
            save_cover=self.config.download.save_cover,
            cover_format=self.config.download.cover_format,
            cover_size=self.config.download.cover_size,
            embed_metadata=self.config.metadata.embed_metadata,
            force_save=force,
            decrypt_timeout_seconds=self.config.download.decrypt_timeout_seconds,
        )

        try:
            self.logger.info(f"[Download] Starting rip_song for song_id={parsed['id']}, storefront={parsed['storefront'] or self.config.region.storefront}")
            result = await rip_song(
                song_id=parsed["id"],
                storefront=parsed["storefront"] or self.config.region.storefront,
                language=self.config.region.language,
                config=rip_config,
                api_client=self._api,
                wrapper_manager=manager,
                progress_callback=progress_callback,
                check_existence=not force,
                plugin_config=self.config,
                playlist=playlist
            )

            self.logger.info(f"[Download] rip_song completed: success={result.success}, status={result.status}, message={result.message}")

            if not result.success:
                return DownloadResult(
                    success=False,
                    message=result.message,
                    error=result.message
                )

            if result.song_data:
                saved = save_all(
                    song_data=result.song_data,
                    codec=result.codec,
                    metadata=result.metadata,
                    config=self.config,
                    lyrics=result.lyrics,
                    cover=result.cover,
                    playlist=playlist
                )

                download_result = DownloadResult(
                    success=True,
                    message="下载成功",
                    file_paths=[saved["song"]] if saved["song"] else [],
                    cover_path=saved.get("cover"),
                    lyrics_path=saved.get("lyrics"),
                    codec=result.codec,
                    track_info={
                        "title": result.metadata.title if result.metadata else None,
                        "artist": result.metadata.artist if result.metadata else None,
                        "album": result.metadata.album if result.metadata else None,
                    }
                )

                self._cache[cache_key] = download_result

                return download_result

            if result.status == DownloadStatus.SKIPPED:
                expected_path = get_output_path(
                    codec=result.codec or codec,
                    metadata=result.metadata,
                    config=self.config,
                    playlist=playlist
                )
                return DownloadResult(
                    success=True,
                    message="文件已存在",
                    file_paths=[expected_path] if Path(expected_path).exists() else [],
                    codec=result.codec
                )

            return DownloadResult(
                success=False,
                message=result.message,
                error="未知错误"
            )

        except Exception as e:
            self.logger.exception(f"Download failed: {e}")
            return DownloadResult(
                success=False,
                message="下载失败",
                error=str(e)
            )

    async def get_song_metadata(
        self,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """获取歌曲元数据但不下载。"""
        parsed = URLParser.parse(url)
        if not parsed or parsed["type"] != URLType.Song:
            return None

        try:
            info = await get_song_info(
                parsed["id"],
                parsed["storefront"] or self.config.region.storefront,
                self.config.region.language,
                self._api
            )
            return info
        except Exception as e:
            self.logger.warning(f"Failed to get song metadata: {e}")
            return None

    def get_download_dirs(self, quality: Optional[DownloadQuality] = None) -> List[Path]:
        """获取下载目录列表。"""
        download_dir = self.config.get_download_path()
        return [download_dir]

    def clear_cache(self):
        """清理下载缓存。"""
        self._cache.clear()
        self.logger.info("Download cache cleared")


# 兼容旧接口
ConfigGenerator = None  # 已废弃，由 core.config 接管
DockerService = None  # 已替换为 WrapperService
