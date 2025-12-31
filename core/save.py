"""
文件保存模块。
负责保存歌曲、歌词与封面。
"""

import logging
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Any

from .metadata import SongMetadata
from .models import PlaylistInfo
from .utils import get_song_name_and_dir_path, get_suffix, get_output_suffix, if_shell


logger = logging.getLogger(__name__)


def _check_file_integrity(file_path: str) -> bool:
    """使用 FFmpeg 校验已落盘文件完整性。"""
    if not shutil.which("ffmpeg"):
        logger.warning(f"未找到 ffmpeg，跳过完整性校验: {file_path}")
        return True

    null_device = "NUL" if not if_shell() else "/dev/null"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", file_path,
            "-c:a", "pcm_s16le",
            "-f", "null", null_device
        ],
        capture_output=True,
        text=True
    )
    if result.stderr:
        logger.warning(f"FFmpeg 完整性校验失败: {result.stderr.strip()[:500]}")
    return not bool(result.stderr)


def _convert_m4a(
    file_path: str,
    target_format: str,
    cover: Optional[bytes],
    cover_format: str,
    keep_original: bool
) -> Optional[str]:
    """将 m4a 转换为指定格式，并尽量保留封面。"""
    if not shutil.which("ffmpeg"):
        logger.warning("未找到 ffmpeg，跳过格式转换")
        return None

    input_path = Path(file_path)
    if input_path.suffix.lower() != ".m4a":
        return None

    format_map = {
        "flac": ".flac",
        "mp3": ".mp3",
        "opus": ".opus",
        "wav": ".wav"
    }
    suffix = format_map.get(target_format.lower())
    if not suffix:
        logger.warning(f"不支持的转换格式: {target_format}")
        return None

    output_path = input_path.with_suffix(suffix)
    cover_codec = "png" if cover_format == "png" else "mjpeg"
    cover_supported = target_format.lower() in {"flac", "mp3"}

    audio_codec_args = {
        "flac": ["-c:a", "flac"],
        "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
        "opus": ["-c:a", "libopus", "-b:a", "192k"],
        "wav": ["-c:a", "pcm_s16le"]
    }

    with TemporaryDirectory() as tmp_dir:
        cmd = ["ffmpeg", "-y", "-i", str(input_path)]
        if cover and cover_supported:
            cover_path = Path(tmp_dir) / f"cover.{cover_format}"
            with open(cover_path, "wb") as f:
                f.write(cover)
            cmd += [
                "-i", str(cover_path),
                "-map", "0:a",
                "-map", "1:v",
                *audio_codec_args[target_format.lower()],
                "-c:v", cover_codec,
                "-disposition:v", "attached_pic",
                "-metadata:s:v", "title=cover",
                "-metadata:s:v", "comment=Cover (front)",
                "-map_metadata", "0"
            ]
            if target_format.lower() == "mp3":
                cmd += ["-id3v2_version", "3"]
        else:
            if cover and not cover_supported:
                logger.warning(f"{target_format} 不支持封面内嵌，已跳过")
            cmd += [
                "-map", "0:a",
                *audio_codec_args[target_format.lower()],
                "-map_metadata", "0"
            ]
            if target_format.lower() == "mp3":
                cmd += ["-id3v2_version", "3"]
        cmd.append(str(output_path))

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not output_path.exists():
            err = result.stderr or result.stdout or "未知错误"
            logger.warning(f"格式转换失败: {err.strip()[:500]}")
            return None

    if not keep_original:
        input_path.unlink(missing_ok=True)
    return str(output_path)


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

    if config.download.convert_after_download:
        converted = _convert_m4a(
            result["song"],
            config.download.convert_format,
            cover,
            config.download.cover_format,
            config.download.convert_keep_original
        )
        if converted:
            result["song"] = converted

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

    if result["song"]:
        _check_file_integrity(result["song"])

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
    suffix = get_output_suffix(
        codec,
        config.download.atmos_convert_to_m4a,
        config.download.convert_after_download,
        config.download.convert_format
    )
    return str(download_dir / dir_path / Path(song_name + suffix))
