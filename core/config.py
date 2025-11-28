"""
Configuration Management for Apple Music Downloader Plugin

This module provides configuration management that bridges AstrBot's JSON Schema
configuration with the internal configuration objects used by the downloader.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WrapperConfig:
    """Wrapper service configuration."""
    mode: str = "docker"  # docker, remote, qemu
    url: str = "127.0.0.1:18923"
    secure: bool = False


@dataclass
class QemuConfig:
    """QEMU local instance configuration."""
    enable_hw_accel: bool = False
    hw_accelerator: str = ""
    memory_size: str = "512M"
    cpu_model: str = "Cascadelake-Server-v5"
    show_window: bool = False


@dataclass
class DockerConfig:
    """Docker container configuration."""
    docker_host: str = ""
    container_name: str = "apple-music-wrapper-manager"
    image_name: str = "apple-music-wrapper-manager"
    grpc_port: int = 18923


@dataclass
class QueueConfig:
    """Download queue configuration."""
    max_queue_size: int = 10
    task_timeout: int = 300
    queue_timeout: int = 600
    notify_progress: bool = True
    notify_queue_position: bool = True
    allow_cancel: bool = True
    max_tasks_per_user: int = 2


@dataclass
class RegionConfig:
    """Region and language configuration."""
    storefront: str = "cn"
    language: str = "zh-Hans-CN"
    language_warning: bool = True


@dataclass
class DownloadConfig:
    """Download settings configuration."""
    default_quality: str = "alac"
    codec_priority: list[str] = field(default_factory=lambda: ["alac", "ec3", "ac3", "aac"])
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


@dataclass
class MetadataConfig:
    """Metadata embedding configuration."""
    embed_metadata: list[str] = field(default_factory=lambda: [
        "title", "artist", "album", "album_artist", "composer", "album_created",
        "genre", "created", "track", "tracknum", "disk", "lyrics", "cover",
        "copyright", "record_company", "upc", "isrc", "rtng"
    ])


@dataclass
class PathConfig:
    """File path configuration."""
    download_dir: str = "downloads"
    song_name_format: str = "{disk}-{tracknum:02d} {title}"
    dir_path_format: str = "{album_artist}/{album}"
    playlist_dir_format: str = "playlists/{playlistName}"
    playlist_song_format: str = "{playlistSongIndex:02d}. {artist} - {title}"


@dataclass
class FileConfig:
    """File management configuration."""
    max_file_size_mb: int = 200
    send_cover: bool = True
    cleanup_interval_hours: int = 1
    file_ttl_hours: int = 24


@dataclass
class PluginConfig:
    """
    Main plugin configuration container.

    This class aggregates all configuration sections and provides
    methods to load from AstrBot's plugin config dict.
    """
    wrapper: WrapperConfig = field(default_factory=WrapperConfig)
    qemu: QemuConfig = field(default_factory=QemuConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    region: RegionConfig = field(default_factory=RegionConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    path: PathConfig = field(default_factory=PathConfig)
    file: FileConfig = field(default_factory=FileConfig)
    debug_mode: bool = False

    # Plugin directory (set at runtime)
    plugin_dir: Optional[Path] = None

    @classmethod
    def from_astrbot_config(cls, config: dict, plugin_dir: Optional[Path] = None) -> "PluginConfig":
        """
        Create a PluginConfig from AstrBot's configuration dictionary.

        Args:
            config: The configuration dictionary from AstrBot
            plugin_dir: The plugin's directory path

        Returns:
            A populated PluginConfig instance
        """
        instance = cls()
        instance.plugin_dir = plugin_dir

        # Wrapper configuration
        instance.wrapper = WrapperConfig(
            mode=config.get("wrapper_mode", "docker"),
            url=config.get("wrapper_url", "127.0.0.1:18923"),
            secure=config.get("wrapper_secure", False),
        )

        # QEMU configuration
        qemu_cfg = config.get("qemu_config", {})
        instance.qemu = QemuConfig(
            enable_hw_accel=qemu_cfg.get("enable_hw_accel", False),
            hw_accelerator=qemu_cfg.get("hw_accelerator", ""),
            memory_size=qemu_cfg.get("memory_size", "512M"),
            cpu_model=qemu_cfg.get("cpu_model", "Cascadelake-Server-v5"),
            show_window=qemu_cfg.get("show_window", False),
        )

        # Docker configuration
        docker_cfg = config.get("docker_config", {})
        instance.docker = DockerConfig(
            docker_host=docker_cfg.get("docker_host", ""),
            container_name=docker_cfg.get("container_name", "apple-music-wrapper-manager"),
            image_name=docker_cfg.get("image_name", "apple-music-wrapper-manager"),
            grpc_port=docker_cfg.get("grpc_port", 18923),
        )

        # Queue configuration
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

        # Region configuration
        region_cfg = config.get("region_config", {})
        instance.region = RegionConfig(
            storefront=region_cfg.get("storefront", "cn"),
            language=region_cfg.get("language", "zh-Hans-CN"),
            language_warning=region_cfg.get("language_warning", True),
        )

        # Download configuration
        download_cfg = config.get("download_config", {})

        # Parse codec priority from comma-separated string
        codec_priority_str = download_cfg.get("codec_priority", "alac,ec3,ac3,aac")
        codec_priority = [c.strip() for c in codec_priority_str.split(",") if c.strip()]

        # Parse lyrics extra from comma-separated string
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
        )

        # Metadata configuration
        metadata_cfg = config.get("metadata_config", {})
        embed_metadata_str = metadata_cfg.get(
            "embed_metadata",
            "title,artist,album,album_artist,composer,album_created,genre,created,track,tracknum,disk,lyrics,cover,copyright,record_company,upc,isrc,rtng"
        )
        embed_metadata = [m.strip() for m in embed_metadata_str.split(",") if m.strip()]
        instance.metadata = MetadataConfig(embed_metadata=embed_metadata)

        # Path configuration
        path_cfg = config.get("path_config", {})
        instance.path = PathConfig(
            download_dir=path_cfg.get("download_dir", "downloads"),
            song_name_format=path_cfg.get("song_name_format", "{disk}-{tracknum:02d} {title}"),
            dir_path_format=path_cfg.get("dir_path_format", "{album_artist}/{album}"),
            playlist_dir_format=path_cfg.get("playlist_dir_format", "playlists/{playlistName}"),
            playlist_song_format=path_cfg.get("playlist_song_format", "{playlistSongIndex:02d}. {artist} - {title}"),
        )

        # File configuration
        file_cfg = config.get("file_config", {})
        instance.file = FileConfig(
            max_file_size_mb=file_cfg.get("max_file_size_mb", 200),
            send_cover=file_cfg.get("send_cover", True),
            cleanup_interval_hours=file_cfg.get("cleanup_interval_hours", 1),
            file_ttl_hours=file_cfg.get("file_ttl_hours", 24),
        )

        # Debug mode
        instance.debug_mode = config.get("debug_mode", False)

        return instance

    def get_download_path(self) -> Path:
        """Get the absolute path to the download directory."""
        download_dir = Path(self.path.download_dir)
        if not download_dir.is_absolute() and self.plugin_dir:
            download_dir = self.plugin_dir / download_dir
        return download_dir

    def get_assets_path(self) -> Path:
        """Get the absolute path to the assets directory."""
        if self.plugin_dir:
            return self.plugin_dir / "assets"
        return Path("assets")
