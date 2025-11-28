"""
Utility Functions for Apple Music Downloader


Provides common utility functions for file handling, codec detection,
lyrics conversion, and path management.
"""

import asyncio
import concurrent.futures
import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Optional, Callable, Any

import m3u8
import regex
from bs4 import BeautifulSoup

from .types import Codec, CodecRegex
from .models import PlaylistInfo


# Thread pool for sync operations
executor_pool = concurrent.futures.ThreadPoolExecutor()


def if_shell() -> bool:
    """Check if shell mode should be used for subprocess calls."""
    if sys.platform in ('win32', 'cygwin', 'cli'):
        return False
    return True


def byte_length(i: int) -> int:
    """Calculate the byte length needed to represent an integer."""
    return (i.bit_length() + 7) // 8


def find_best_codec(
    parsed_m3u8: m3u8.M3U8,
    codec: str,
    max_bit_depth: int = 24,
    max_sample_rate: int = 192000
) -> Optional[m3u8.Playlist]:
    """
    Find the best matching codec playlist from M3U8.

    Args:
        parsed_m3u8: Parsed M3U8 object
        codec: Target codec
        max_bit_depth: Maximum bit depth for ALAC
        max_sample_rate: Maximum sample rate for ALAC

    Returns:
        Best matching playlist or None
    """
    available_medias = [
        playlist for playlist in parsed_m3u8.playlists
        if regex.match(CodecRegex.get_pattern_by_codec(codec), playlist.stream_info.audio)
    ]
    available_medias.sort(key=lambda x: x.stream_info.average_bandwidth, reverse=True)

    if codec == Codec.ALAC:
        limited_medias = [
            media for media in available_medias
            if int(media.media[0].extras.get("bit_depth", 0)) <= max_bit_depth
            and int(media.media[0].extras.get("sample_rate", 0)) <= max_sample_rate
        ]
    else:
        limited_medias = available_medias

    if not limited_medias:
        return None
    return limited_medias[0]


def chunk(it, size: int):
    """Split an iterable into chunks of specified size."""
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


def get_digit_from_string(text: str) -> int:
    """Extract digits from a string and convert to integer."""
    return int(''.join(filter(str.isdigit, text)))


def ttml_convent(
    ttml: str,
    lyrics_format: str = "lrc",
    lyrics_extra: list[str] = None
) -> str:
    """
    Convert TTML lyrics to LRC format.

    Args:
        ttml: TTML lyrics content
        lyrics_format: Target format (ttml or lrc)
        lyrics_extra: Extra lyrics to include (translation, pronunciation)

    Returns:
        Converted lyrics string
    """
    if lyrics_format == "ttml":
        return ttml

    if lyrics_extra is None:
        lyrics_extra = []

    b = BeautifulSoup(ttml, features="xml")
    lrc_lines = []

    for item in b.tt.body.children:
        for lyric in item.children:
            h, m, s, ms = 0, 0, 0, 0
            lyric_time: str = lyric.get("begin")
            if not lyric_time:
                return ""

            if lyric_time.find('.') == -1:
                lyric_time += '.000'

            match lyric_time.count(":"):
                case 0:
                    split_time = lyric_time.split(".")
                    s, ms = get_digit_from_string(split_time[0]), get_digit_from_string(split_time[1])
                case 1:
                    split_time = lyric_time.split(":")
                    s_ms = split_time[-1]
                    del split_time[-1]
                    split_time.extend(s_ms.split("."))
                    m, s, ms = (
                        get_digit_from_string(split_time[0]),
                        get_digit_from_string(split_time[1]),
                        get_digit_from_string(split_time[2])
                    )
                case 2:
                    split_time = lyric_time.split(":")
                    s_ms = split_time[-1]
                    del split_time[-1]
                    split_time.extend(s_ms.split("."))
                    h, m, s, ms = (
                        get_digit_from_string(split_time[0]),
                        get_digit_from_string(split_time[1]),
                        get_digit_from_string(split_time[2]),
                        get_digit_from_string(split_time[3])
                    )

            timestamp = f"[{str(m + h * 60).rjust(2, '0')}:{str(s).rjust(2, '0')}.{str(int(ms / 10)).rjust(2, '0')}]"
            lrc_lines.append(f"{timestamp}{lyric.text}")

            # Handle translation
            if "translation" in lyrics_extra and b.tt.head.metadata.iTunesMetadata.translation:
                for translation in b.tt.head.metadata.iTunesMetadata.translation.children:
                    if lyric.get("itunes:key") == translation.get("for"):
                        lrc_lines.append(f"{timestamp}{translation.text}")

            # Handle pronunciation (transliteration)
            if "pronunciation" in lyrics_extra and b.tt.head.metadata.iTunesMetadata.transliteration:
                for transliteration in b.tt.head.metadata.iTunesMetadata.transliteration.children:
                    if lyric.get("itunes:key") == transliteration.get("for"):
                        lrc_lines.append(f"{timestamp}{transliteration.text}")

    return "\n".join(lrc_lines)


def get_valid_filename(filename: str) -> str:
    """Remove invalid characters from filename."""
    return "".join(i for i in filename if i not in ["<", ">", ":", "\"", "/", "\\", "|", "?", "*"])


def get_valid_dir_name(dirname: str) -> str:
    """Remove invalid characters from directory name."""
    return regex.sub(r"\.+$", "", get_valid_filename(dirname))


def get_codec_from_codec_id(codec_id: str) -> str:
    """
    Get codec type from codec ID string.

    Args:
        codec_id: Codec identifier string

    Returns:
        Codec constant or empty string
    """
    codecs = [
        Codec.AC3, Codec.EC3, Codec.AAC, Codec.ALAC,
        Codec.AAC_BINAURAL, Codec.AAC_DOWNMIX
    ]
    for codec in codecs:
        if regex.match(CodecRegex.get_pattern_by_codec(codec), codec_id):
            return codec
    return ""


def get_song_id_from_m3u8(m3u8_url: str) -> str:
    """Extract song ID from M3U8 URL."""
    parsed_m3u8 = m3u8.load(m3u8_url)
    return regex.search(r"_A(\d*)_", parsed_m3u8.playlists[0].uri)[1]


def if_raw_atmos(codec: str, convert_atmos: bool) -> bool:
    """Check if output should be raw Atmos format."""
    if (codec == Codec.EC3 or codec == Codec.AC3) and not convert_atmos:
        return True
    return False


def get_suffix(codec: str, convert_atmos: bool) -> str:
    """
    Get file suffix based on codec and conversion settings.

    Args:
        codec: Audio codec
        convert_atmos: Whether to convert Atmos to M4A

    Returns:
        File extension string
    """
    if not convert_atmos and codec == Codec.EC3:
        return ".ec3"
    elif not convert_atmos and codec == Codec.AC3:
        return ".ac3"
    else:
        return ".m4a"


def playlist_metadata_to_params(playlist: PlaylistInfo) -> dict:
    """Extract playlist metadata for path formatting."""
    return {
        "playlistName": playlist.data[0].attributes.name,
        "playlistCuratorName": playlist.data[0].attributes.curatorName
    }


def get_path_safe_dict(param: dict) -> dict:
    """Make all string values in dict safe for file paths."""
    new_param = deepcopy(param)
    for key, val in new_param.items():
        if isinstance(val, str):
            new_param[key] = get_valid_filename(str(val))
    return new_param


def get_song_name_and_dir_path(
    codec: str,
    metadata: Any,
    config: Any,
    playlist: PlaylistInfo = None
) -> tuple[str, Path]:
    """
    Generate song filename and directory path from metadata.

    Args:
        codec: Audio codec
        metadata: Song metadata object
        config: Plugin configuration
        playlist: Playlist info (optional)

    Returns:
        Tuple of (song_name, dir_path)
    """
    safe_meta = get_path_safe_dict(metadata.model_dump())

    if playlist:
        safe_pl_meta = get_path_safe_dict(playlist_metadata_to_params(playlist))
        song_name = config.path.playlist_song_format.format(
            codec=codec,
            playlistSongIndex=metadata.playlist_index,
            **safe_meta,
            **safe_pl_meta
        )
        dir_path = Path(config.path.playlist_dir_format.format(
            codec=codec,
            **safe_meta,
            **safe_pl_meta
        ))
    else:
        song_name = config.path.song_name_format.format(
            codec=codec,
            **safe_meta
        )
        dir_path = Path(config.path.dir_path_format.format(
            codec=codec,
            **safe_meta
        ))

    song_name = get_valid_filename(song_name)
    is_abs = dir_path.is_absolute()
    sanitized_parts = [
        part if i == 0 and is_abs else get_valid_dir_name(part)
        for i, part in enumerate(dir_path.parts)
    ]
    dir_path = Path(*sanitized_parts) if sanitized_parts else Path(".")

    return song_name, dir_path


def check_song_exists(
    metadata: Any,
    codec: str,
    config: Any,
    playlist: PlaylistInfo = None
) -> bool:
    """
    Check if a song file already exists.

    Args:
        metadata: Song metadata
        codec: Audio codec
        config: Plugin configuration
        playlist: Playlist info (optional)

    Returns:
        True if file exists
    """
    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, config, playlist)
    download_dir = config.get_download_path()
    full_path = download_dir / dir_path / Path(
        song_name + get_suffix(codec, config.download.atmos_convert_to_m4a)
    )
    return full_path.exists()


def playlist_write_song_index(playlist: PlaylistInfo) -> PlaylistInfo:
    """Write song index mapping to playlist info."""
    for track_index, track in enumerate(playlist.data[0].relationships.tracks.data):
        playlist.songIdIndexMapping[track.id] = track_index + 1
    return playlist


def convert_mac_timestamp_to_datetime(timestamp: int) -> datetime:
    """Convert Mac timestamp to datetime object."""
    d = datetime.strptime("01-01-1904", "%m-%d-%Y")
    return d + timedelta(seconds=timestamp)


def check_dependencies(deps: list[str] = None) -> tuple[bool, Optional[str]]:
    """
    Check if required external dependencies are available.

    Args:
        deps: List of dependency commands to check

    Returns:
        Tuple of (success, missing_dep_name)
    """
    if deps is None:
        deps = ["ffmpeg", "gpac", "MP4Box", "mp4edit", "mp4extract", "mp4decrypt"]

    for dep in deps:
        try:
            subprocess.run(
                dep,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=if_shell()
            )
        except FileNotFoundError:
            return False, dep
    return True, None


async def run_sync(task: Callable, *args) -> Any:
    """
    Run a synchronous function in the executor pool.

    Args:
        task: Synchronous callable
        *args: Arguments to pass to the function

    Returns:
        Function result
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor_pool, task, *args)


def query_language(region: str, storefronts_path: str = "assets/storefronts.json") -> Optional[tuple[str, list[str]]]:
    """
    Query default and supported languages for a region.

    Args:
        region: Region/storefront code
        storefronts_path: Path to storefronts JSON file

    Returns:
        Tuple of (default_language, supported_languages) or None
    """
    try:
        with open(storefronts_path, "r", encoding="utf-8") as f:
            storefronts = json.load(f)
            for storefront in storefronts["data"]:
                if storefront["id"].upper() == region.upper():
                    return (
                        storefront["attributes"]["defaultLanguageTag"],
                        storefront["attributes"]["supportedLanguageTags"]
                    )
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    return None


def language_exist(region: str, language: str, storefronts_path: str = "assets/storefronts.json") -> bool:
    """
    Check if a language is supported in a region.

    Args:
        region: Region/storefront code
        language: Language tag to check
        storefronts_path: Path to storefronts JSON file

    Returns:
        True if language is supported
    """
    result = query_language(region, storefronts_path)
    if result is None:
        return False
    _, languages = result
    return language in languages
