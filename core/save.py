"""
文件保存模块。
负责保存歌曲、歌词与封面。
"""

import logging
from pathlib import Path
from typing import Optional, Any

from .metadata import SongMetadata
from .models import PlaylistInfo
from .utils import get_song_name_and_dir_path, get_suffix


logger = logging.getLogger(__name__)


def save_song(
    song_data: bytes,
    codec: str,
    metadata: SongMetadata,
    config: Any,
    playlist: PlaylistInfo = None
) -> str:
    """保存歌曲到本地。"""
    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, config, playlist)
    download_dir = config.get_download_path()

    full_dir = download_dir / dir_path
    full_dir.mkdir(parents=True, exist_ok=True)

    suffix = get_suffix(codec, config.download.atmos_convert_to_m4a)
    file_path = full_dir / Path(song_name + suffix)

    with open(file_path, "wb") as f:
        f.write(song_data)

    logger.info(f"Saved song to: {file_path}")
    return str(file_path)


def save_lyrics(
    lyrics: str,
    codec: str,
    metadata: SongMetadata,
    config: Any,
    playlist: PlaylistInfo = None,
    lyrics_format: str = "lrc"
) -> Optional[str]:
    """保存歌词到本地。"""
    if not lyrics:
        return None

    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, config, playlist)
    download_dir = config.get_download_path()

    full_dir = download_dir / dir_path
    full_dir.mkdir(parents=True, exist_ok=True)

    suffix = f".{lyrics_format}"
    file_path = full_dir / Path(song_name + suffix)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(lyrics)

    logger.info(f"Saved lyrics to: {file_path}")
    return str(file_path)


def save_cover(
    cover_data: bytes,
    codec: str,
    metadata: SongMetadata,
    config: Any,
    playlist: PlaylistInfo = None,
    cover_format: str = "jpg"
) -> Optional[str]:
    """保存封面到本地。"""
    if not cover_data:
        return None

    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, config, playlist)
    download_dir = config.get_download_path()

    full_dir = download_dir / dir_path
    full_dir.mkdir(parents=True, exist_ok=True)

    suffix = f".{cover_format}"
    file_path = full_dir / Path("cover" + suffix)

    if not file_path.exists():
        with open(file_path, "wb") as f:
            f.write(cover_data)
        logger.info(f"Saved cover to: {file_path}")

    return str(file_path)


def save_all(
    song_data: bytes,
    codec: str,
    metadata: SongMetadata,
    config: Any,
    lyrics: Optional[str] = None,
    cover: Optional[bytes] = None,
    playlist: PlaylistInfo = None
) -> dict:
    """保存歌曲、歌词与封面。"""
    result = {
        "song": None,
        "lyrics": None,
        "cover": None
    }

    result["song"] = save_song(song_data, codec, metadata, config, playlist)

    if config.download.save_lyrics and lyrics:
        result["lyrics"] = save_lyrics(
            lyrics, codec, metadata, config, playlist,
            config.download.lyrics_format
        )

    if config.download.save_cover and cover:
        result["cover"] = save_cover(
            cover, codec, metadata, config, playlist,
            config.download.cover_format
        )

    return result


def get_output_path(
    codec: str,
    metadata: SongMetadata,
    config: Any,
    playlist: PlaylistInfo = None
) -> str:
    """获取歌曲预期输出路径（不落盘）。"""
    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, config, playlist)
    download_dir = config.get_download_path()
    suffix = get_suffix(codec, config.download.atmos_convert_to_m4a)
    return str(download_dir / dir_path / Path(song_name + suffix))
