"""
Apple Music Downloader Core Module


This module provides the core functionality for downloading music from Apple Music,
including:
- gRPC communication with the Wrapper Manager service
- Apple Music URL parsing
- Apple Music Web API client
- Data models for API responses
- Audio codec handling
- MP4 processing and metadata embedding
- Download workflow management
"""

# Types and constants
from .types import (
    Codec,
    CodecKeySuffix,
    CodecRegex,
    M3U8Info,
    SongInfo,
    SampleInfo,
    ParentDoneHandler,
    DEFAULT_ID,
    PREFETCH_KEY,
)

# gRPC client
from .grpc import WrapperManager, WrapperManagerException

# Data models
from .models import (
    SongData,
    AlbumMeta,
    Tracks,
    AlbumTracks,
    ArtistInfo,
    ArtistAlbums,
    ArtistSongs,
    PlaylistInfo,
    PlaylistTracks,
    SongLyrics,
    TracksMeta,
)

# URL parsing
from .url import (
    AppleMusicURL,
    Song,
    Album,
    Playlist,
    Artist,
    URLType,
)

# API client
from .api import WebAPI

# Configuration
from .config import (
    PluginConfig,
    WrapperConfig,
    QemuConfig,
    DockerConfig,
    QueueConfig,
    RegionConfig,
    DownloadConfig,
    MetadataConfig,
    PathConfig,
    FileConfig,
)

# Metadata
from .metadata import SongMetadata

# MP4 processing
from .mp4 import (
    extract_media,
    extract_song,
    encapsulate,
    write_metadata,
    fix_encapsulate,
    fix_esds_box,
    check_song_integrity,
    get_available_codecs,
    CodecNotFoundException,
)

# Download core
from .rip import (
    rip_song,
    get_song_info,
    DownloadTask,
    DownloadStatus,
    DownloadResult,
    DownloadConfig as RipDownloadConfig,
)

# File saving
from .save import (
    save_song,
    save_lyrics,
    save_cover,
    save_all,
    get_output_path,
)

# Utilities
from .utils import (
    ttml_convent,
    get_valid_filename,
    get_codec_from_codec_id,
    get_suffix,
    check_song_exists,
    check_dependencies,
    query_language,
    language_exist,
    run_sync,
)


__all__ = [
    # Types
    "Codec",
    "CodecKeySuffix",
    "CodecRegex",
    "M3U8Info",
    "SongInfo",
    "SampleInfo",
    "ParentDoneHandler",
    "DEFAULT_ID",
    "PREFETCH_KEY",
    # gRPC
    "WrapperManager",
    "WrapperManagerException",
    # Models
    "SongData",
    "AlbumMeta",
    "Tracks",
    "AlbumTracks",
    "ArtistInfo",
    "ArtistAlbums",
    "ArtistSongs",
    "PlaylistInfo",
    "PlaylistTracks",
    "SongLyrics",
    "TracksMeta",
    # URL
    "AppleMusicURL",
    "Song",
    "Album",
    "Playlist",
    "Artist",
    "URLType",
    # API
    "WebAPI",
    # Config
    "PluginConfig",
    "WrapperConfig",
    "QemuConfig",
    "DockerConfig",
    "QueueConfig",
    "RegionConfig",
    "DownloadConfig",
    "MetadataConfig",
    "PathConfig",
    "FileConfig",
    # Metadata
    "SongMetadata",
    # MP4
    "extract_media",
    "extract_song",
    "encapsulate",
    "write_metadata",
    "fix_encapsulate",
    "fix_esds_box",
    "check_song_integrity",
    "get_available_codecs",
    "CodecNotFoundException",
    # Rip
    "rip_song",
    "get_song_info",
    "DownloadTask",
    "DownloadStatus",
    "DownloadResult",
    "RipDownloadConfig",
    # Save
    "save_song",
    "save_lyrics",
    "save_cover",
    "save_all",
    "get_output_path",
    # Utils
    "ttml_convent",
    "get_valid_filename",
    "get_codec_from_codec_id",
    "get_suffix",
    "check_song_exists",
    "check_dependencies",
    "query_language",
    "language_exist",
    "run_sync",
]
