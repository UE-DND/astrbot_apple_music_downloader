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

from .grpc import WrapperManager, WrapperManagerException

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

from .url import (
    AppleMusicURL,
    Song,
    Album,
    Playlist,
    Artist,
    URLType,
)

from .api import WebAPI

from .config import (
    PluginConfig,
    WrapperConfig,
    QueueConfig,
    RegionConfig,
    DownloadConfig,
    MetadataConfig,
    PathConfig,
    FileConfig,
)

from .metadata import SongMetadata

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

from .rip import (
    rip_song,
    get_song_info,
    DownloadTask,
    DownloadStatus,
    DownloadResult,
    DownloadConfig as RipDownloadConfig,
)

from .save import (
    save_song,
    save_lyrics,
    save_cover,
    save_all,
    get_output_path,
)

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
    "Codec",
    "CodecKeySuffix",
    "CodecRegex",
    "M3U8Info",
    "SongInfo",
    "SampleInfo",
    "ParentDoneHandler",
    "DEFAULT_ID",
    "PREFETCH_KEY",
    "WrapperManager",
    "WrapperManagerException",
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
    "AppleMusicURL",
    "Song",
    "Album",
    "Playlist",
    "Artist",
    "URLType",
    "WebAPI",
    "PluginConfig",
    "WrapperConfig",
    "QueueConfig",
    "RegionConfig",
    "DownloadConfig",
    "MetadataConfig",
    "PathConfig",
    "FileConfig",
    "SongMetadata",
    "extract_media",
    "extract_song",
    "encapsulate",
    "write_metadata",
    "fix_encapsulate",
    "fix_esds_box",
    "check_song_integrity",
    "get_available_codecs",
    "CodecNotFoundException",
    "rip_song",
    "get_song_info",
    "DownloadTask",
    "DownloadStatus",
    "DownloadResult",
    "RipDownloadConfig",
    "save_song",
    "save_lyrics",
    "save_cover",
    "save_all",
    "get_output_path",
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
