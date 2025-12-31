"""
Apple Music 下载器工具函数。
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


# 同步任务线程池
executor_pool = concurrent.futures.ThreadPoolExecutor()


def if_shell() -> bool:
    """判断子进程是否需要 shell 模式。"""
    if sys.platform in ('win32', 'cygwin', 'cli'):
        return False
    return True


def byte_length(i: int) -> int:
    """计算表示整数所需字节数。"""
    return (i.bit_length() + 7) // 8


def find_best_codec(
    parsed_m3u8: m3u8.M3U8,
    codec: str,
    max_bit_depth: int = 24,
    max_sample_rate: int = 192000
) -> Optional[m3u8.Playlist]:
    """从 M3U8 中选择最合适的编码播放列表。"""
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
    """将可迭代对象按固定大小分块。"""
    it = iter(it)
    return iter(lambda: tuple(islice(it, size)), ())


def get_digit_from_string(text: str) -> int:
    """提取字符串中的数字并转为整数。"""
    return int(''.join(filter(str.isdigit, text)))


def ttml_convent(
    ttml: str,
    lyrics_format: str = "lrc",
    lyrics_extra: list[str] = None
) -> str:
    """将 TTML 歌词转换为 LRC/TTML。"""
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

            # 处理翻译歌词
            if "translation" in lyrics_extra and b.tt.head.metadata.iTunesMetadata.translation:
                trans_type = b.tt.head.metadata.iTunesMetadata.translation.get("type")
                for translation in b.tt.head.metadata.iTunesMetadata.translation.children:
                    if lyric.get("itunes:key") == translation.get("for"):
                        if trans_type == "replacement":
                            del lrc_lines[-1]
                        lrc_lines.append(f"{timestamp}{translation.text}")

            # 处理注音歌词
            if "pronunciation" in lyrics_extra and b.tt.head.metadata.iTunesMetadata.transliteration:
                for transliteration in b.tt.head.metadata.iTunesMetadata.transliteration.children:
                    if lyric.get("itunes:key") == transliteration.get("for"):
                        lrc_lines.append(f"{timestamp}{transliteration.text}")

    return "\n".join(lrc_lines)


def get_valid_filename(filename: str) -> str:
    """清理文件名中的非法字符。"""
    return "".join(i for i in filename if i not in ["<", ">", ":", "\"", "/", "\\", "|", "?", "*"])


def get_valid_dir_name(dirname: str) -> str:
    """清理目录名中的非法字符。"""
    return regex.sub(r"\.+$", "", get_valid_filename(dirname))


def get_codec_from_codec_id(codec_id: str) -> str:
    """根据 codec_id 解析编码类型。"""
    codecs = [
        Codec.AC3, Codec.EC3, Codec.AAC, Codec.ALAC,
        Codec.AAC_BINAURAL, Codec.AAC_DOWNMIX
    ]
    for codec in codecs:
        if regex.match(CodecRegex.get_pattern_by_codec(codec), codec_id):
            return codec
    return ""


def get_song_id_from_m3u8(m3u8_url: str) -> str:
    """从 M3U8 URL 提取歌曲 ID。"""
    parsed_m3u8 = m3u8.load(m3u8_url)
    return regex.search(r"_A(\d*)_", parsed_m3u8.playlists[0].uri)[1]


def if_raw_atmos(codec: str, convert_atmos: bool) -> bool:
    """判断是否输出原始 Atmos。"""
    if (codec == Codec.EC3 or codec == Codec.AC3) and not convert_atmos:
        return True
    return False


def get_suffix(codec: str, convert_atmos: bool) -> str:
    """根据编码与 Atmos 设置返回文件后缀。"""
    if not convert_atmos and codec == Codec.EC3:
        return ".ec3"
    elif not convert_atmos and codec == Codec.AC3:
        return ".ac3"
    else:
        return ".m4a"


def get_output_suffix(
    codec: str,
    convert_atmos: bool,
    convert_after_download: bool = False,
    convert_format: str = ""
) -> str:
    """根据配置返回最终输出文件后缀。"""
    suffix = get_suffix(codec, convert_atmos)
    if convert_after_download and suffix == ".m4a":
        format_map = {
            "flac": ".flac",
            "mp3": ".mp3",
            "opus": ".opus",
            "wav": ".wav"
        }
        mapped = format_map.get(convert_format.lower())
        if mapped:
            return mapped
    return suffix


def playlist_metadata_to_params(playlist: PlaylistInfo) -> dict:
    """提取歌单元数据用于路径格式化。"""
    return {
        "playlistName": playlist.data[0].attributes.name,
        "playlistCuratorName": playlist.data[0].attributes.curatorName
    }


def get_path_safe_dict(param: dict) -> dict:
    """将字典中的字符串值转换为安全路径格式。"""
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
    """从元数据生成歌曲文件名与目录路径。"""
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
    """检查歌曲文件是否已存在。"""
    song_name, dir_path = get_song_name_and_dir_path(codec, metadata, config, playlist)
    download_dir = config.get_download_path()
    suffix = get_output_suffix(
        codec,
        config.download.atmos_convert_to_m4a,
        config.download.convert_after_download,
        config.download.convert_format
    )
    full_path = download_dir / dir_path / Path(song_name + suffix)
    return full_path.exists()


def playlist_write_song_index(playlist: PlaylistInfo) -> PlaylistInfo:
    """写入歌单歌曲索引映射。"""
    for track_index, track in enumerate(playlist.data[0].relationships.tracks.data):
        playlist.songIdIndexMapping[track.id] = track_index + 1
    return playlist


def convert_mac_timestamp_to_datetime(timestamp: int) -> datetime:
    """将 Mac 时间戳转换为 datetime。"""
    d = datetime.strptime("01-01-1904", "%m-%d-%Y")
    return d + timedelta(seconds=timestamp)


def check_dependencies(deps: list[str] = None) -> tuple[bool, Optional[str]]:
    """检查外部依赖是否可用。"""
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
    """在线程池中执行同步函数。"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor_pool, task, *args)


def query_language(region: str, storefronts_path: str = "assets/storefronts.json") -> Optional[tuple[str, list[str]]]:
    """查询地区默认语言与支持语言。"""
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
    """检查地区是否支持指定语言。"""
    result = query_language(region, storefronts_path)
    if result is None:
        return False
    _, languages = result
    return language in languages
