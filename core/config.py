"""
Apple Music Downloader 插件配置管理。
桥接 AstrBot 配置与内部配置对象。
"""

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


@dataclass
class WrapperConfig:
    """用于 Wrapper 的服务配置。"""
    url: str = "127.0.0.1:18923"
    secure: bool = False


@dataclass
class QueueConfig:
    """下载队列配置。"""
    max_queue_size: int = 10
    task_timeout: int = 300
    queue_timeout: int = 600
    notify_progress: bool = True
    notify_queue_position: bool = True
    allow_cancel: bool = True
    max_tasks_per_user: int = 2


@dataclass
class RegionConfig:
    """地区与语言配置。"""
    storefront: str = "cn"
    language: str = "zh-Hans-CN"
    language_warning: bool = True


@dataclass
class DownloadConfig:
    """下载设置配置。"""
    default_quality: str = "alac"
    codec_priority: list[str] = field(default_factory=lambda: ["alac", "aac"])
    codec_alternative: bool = True
    max_sample_rate: int = 192000
    max_bit_depth: int = 24
    atmos_convert_to_m4a: bool = True
    save_lyrics: bool = True
    lyrics_format: str = "lrc"
    lyrics_extra: list[str] = field(default_factory=lambda: ["translation", "pronunciation"])
    save_cover: bool = True
    cover_format: str = "jpg"
    cover_size: str = "5000x5000"
    convert_after_download: bool = True
    convert_format: str = "flac"
    convert_keep_original: bool = False
    decrypt_timeout_seconds: int = 600


@dataclass
class MetadataConfig:
    """元数据写入配置。"""
    embed_metadata: list[str] = field(default_factory=lambda: [
        "title", "artist", "album", "album_artist", "composer", "album_created",
        "genre", "created", "track", "tracknum", "disk", "lyrics", "cover",
        "copyright", "record_company", "upc", "isrc", "rtng"
    ])


@dataclass
class PathConfig:
    """文件路径配置。"""
    download_dir: str = "plugin_data/astrbot_plugin_applemusicdownloader/downloads"
    song_name_format: str = "{disk}-{tracknum:02d} {title}"
    dir_path_format: str = "{album_artist}/{album}"
    playlist_dir_format: str = "playlists/{playlistName}"
    playlist_song_format: str = "{playlistSongIndex:02d}. {artist} - {title}"


@dataclass
class FileConfig:
    """文件管理配置。"""
    max_file_size_mb: int = 200
    send_cover: bool = True
    cleanup_interval_hours: int = 1
    file_ttl_hours: int = 24


@dataclass
class PluginConfig:
    """插件主配置容器。"""
    wrapper: WrapperConfig = field(default_factory=WrapperConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    region: RegionConfig = field(default_factory=RegionConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    path: PathConfig = field(default_factory=PathConfig)
    file: FileConfig = field(default_factory=FileConfig)
    debug_mode: bool = False

    # 插件目录（运行时设置）
    plugin_dir: Optional[Path] = None

    @classmethod
    def from_astrbot_config(cls, config: dict, plugin_dir: Optional[Path] = None) -> "PluginConfig":
        """从 AstrBot 配置字典构建 PluginConfig。"""
        instance = cls()
        instance.plugin_dir = plugin_dir

        # Wrapper 配置
        instance.wrapper = WrapperConfig(
            url=config.get("wrapper_url", "127.0.0.1:18923"),
            secure=config.get("wrapper_secure", False),
        )

        # 队列配置
        queue_cfg = config.get("queue_config", {})
        instance.queue = QueueConfig(
            max_queue_size=queue_cfg.get("max_queue_size", 10),
            task_timeout=queue_cfg.get("task_timeout", 300),
            queue_timeout=queue_cfg.get("queue_timeout", 600),
            notify_progress=queue_cfg.get("notify_progress", True),
            notify_queue_position=queue_cfg.get("notify_queue_position", True),
            allow_cancel=queue_cfg.get("allow_cancel", True),
            max_tasks_per_user=queue_cfg.get("max_tasks_per_user", 2),
        )

        # 区域配置
        region_cfg = config.get("region_config", {})
        instance.region = RegionConfig(
            storefront=region_cfg.get("storefront", "cn"),
            language=region_cfg.get("language", "zh-Hans-CN"),
            language_warning=region_cfg.get("language_warning", True),
        )

        # 下载配置
        download_cfg = config.get("download_config", {})

        # 解析音质优先级（逗号分隔）
        codec_priority_str = download_cfg.get("codec_priority", "alac,aac")
        codec_priority = [c.strip() for c in codec_priority_str.split(",") if c.strip()]

        # 解析歌词扩展（逗号分隔）
        lyrics_extra_str = download_cfg.get("lyrics_extra", "translation,pronunciation")
        lyrics_extra = [e.strip() for e in lyrics_extra_str.split(",") if e.strip()]

        instance.download = DownloadConfig(
            default_quality=download_cfg.get("default_quality", "alac"),
            codec_priority=codec_priority,
            codec_alternative=download_cfg.get("codec_alternative", True),
            max_sample_rate=download_cfg.get("max_sample_rate", 192000),
            max_bit_depth=download_cfg.get("max_bit_depth", 24),
            atmos_convert_to_m4a=download_cfg.get("atmos_convert_to_m4a", True),
            save_lyrics=download_cfg.get("save_lyrics", True),
            lyrics_format=download_cfg.get("lyrics_format", "lrc"),
            lyrics_extra=lyrics_extra,
            save_cover=download_cfg.get("save_cover", True),
            cover_format=download_cfg.get("cover_format", "jpg"),
            cover_size=download_cfg.get("cover_size", "5000x5000"),
            convert_after_download=download_cfg.get("convert_after_download", True),
            convert_format=download_cfg.get("convert_format", "flac"),
            convert_keep_original=download_cfg.get("convert_keep_original", False),
            decrypt_timeout_seconds=download_cfg.get("decrypt_timeout_seconds", 600),
        )

        # 元数据配置
        metadata_cfg = config.get("metadata_config", {})
        embed_metadata_str = metadata_cfg.get(
            "embed_metadata",
            "title,artist,album,album_artist,composer,album_created,genre,created,track,tracknum,disk,lyrics,cover,copyright,record_company,upc,isrc,rtng"
        )
        embed_metadata = [m.strip() for m in embed_metadata_str.split(",") if m.strip()]
        instance.metadata = MetadataConfig(embed_metadata=embed_metadata)

        # 路径配置
        path_cfg = config.get("path_config", {})
        instance.path = PathConfig(
            download_dir=path_cfg.get("download_dir", "downloads"),
            song_name_format=path_cfg.get("song_name_format", "{disk}-{tracknum:02d} {title}"),
            dir_path_format=path_cfg.get("dir_path_format", "{album_artist}/{album}"),
            playlist_dir_format=path_cfg.get("playlist_dir_format", "playlists/{playlistName}"),
            playlist_song_format=path_cfg.get("playlist_song_format", "{playlistSongIndex:02d}. {artist} - {title}"),
        )

        # 文件配置
        file_cfg = config.get("file_config", {})
        instance.file = FileConfig(
            max_file_size_mb=file_cfg.get("max_file_size_mb", 200),
            send_cover=file_cfg.get("send_cover", True),
            cleanup_interval_hours=file_cfg.get("cleanup_interval_hours", 1),
            file_ttl_hours=file_cfg.get("file_ttl_hours", 24),
        )

        # 调试模式
        instance.debug_mode = config.get("debug_mode", False)

        return instance

    def get_download_path(self) -> Path:
        """获取下载目录的绝对路径。"""
        download_dir = Path(self.path.download_dir)
        if not download_dir.is_absolute():
            data_dir = self._resolve_astrbot_data_dir()
            plugin_data_dir = self._resolve_plugin_data_dir(data_dir)

            if data_dir and download_dir.parts and download_dir.parts[0] == "plugin_data":
                download_dir = data_dir / download_dir
            elif plugin_data_dir:
                download_dir = plugin_data_dir / download_dir
            elif self.plugin_dir:
                download_dir = self.plugin_dir / download_dir
        return download_dir

    def _resolve_astrbot_data_dir(self) -> Optional[Path]:
        """尝试解析 AstrBot 的 data 目录。"""
        try:
            import importlib
            module = importlib.import_module("astrbot.core.utils.astrbot_path")
            return module.get_astrbot_data_path()
        except (ImportError, AttributeError, OSError) as exc:
            logger.debug(
                "解析 AstrBot data 目录失败 stage=import_astrbot_path exc_type=%s",
                type(exc).__name__,
                exc_info=True,
            )

        if not self.plugin_dir:
            return None

        plugins_dir = self.plugin_dir.parent
        if plugins_dir.name != "plugins":
            return None

        data_dir = plugins_dir.parent
        if data_dir.name != "data":
            return None

        return data_dir

    def _resolve_plugin_data_dir(self, data_dir: Optional[Path]) -> Optional[Path]:
        """获取插件的数据目录路径。"""
        if not data_dir:
            return None

        plugin_name = self._resolve_plugin_name()
        return data_dir / "plugin_data" / plugin_name

    def _resolve_plugin_name(self) -> str:
        """解析插件名称。"""
        if self.plugin_dir:
            return self.plugin_dir.name
        return "astrbot_plugin_applemusicdownloader"

    def get_assets_path(self) -> Path:
        """获取资源目录的绝对路径。"""
        if self.plugin_dir:
            return self.plugin_dir / "assets"
        return Path("assets")
