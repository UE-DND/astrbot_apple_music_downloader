"""
MP4 处理模块。
负责提取、封装与写入元数据。
"""

import os
import subprocess
import uuid
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Tuple, Optional, Any

import m3u8
import regex
import mutagen.mp4
from bs4 import BeautifulSoup

from .types import (
    Codec, CodecRegex, CodecKeySuffix, M3U8Info,
    SongInfo, SampleInfo, PREFETCH_KEY
)
from .metadata import SongMetadata
from .utils import (
    find_best_codec, get_codec_from_codec_id, get_suffix,
    convert_mac_timestamp_to_datetime, if_raw_atmos, if_shell
)


class CodecNotFoundException(Exception):
    """请求的编码不可用时抛出。"""
    pass


async def get_available_codecs(m3u8_content: str, m3u8_url: str) -> Tuple[list[str], list[str]]:
    """从 M3U8 中获取可用编码。"""
    parsed_m3u8 = m3u8.loads(m3u8_content, uri=m3u8_url)
    codec_ids = [playlist.stream_info.audio for playlist in parsed_m3u8.playlists]
    codecs = [get_codec_from_codec_id(codec_id) for codec_id in codec_ids]
    return codecs, codec_ids


async def extract_media(
    m3u8_content: str,
    m3u8_url: str,
    codec: str,
    codec_priority: list[str],
    codec_alternative: bool,
    max_bit_depth: int = 24,
    max_sample_rate: int = 192000,
    download_m3u8_func=None
) -> Tuple[M3U8Info, str]:
    """从 M3U8 提取媒体信息。"""
    parsed_m3u8 = m3u8.loads(m3u8_content, uri=m3u8_url)
    specify_playlist = find_best_codec(parsed_m3u8, codec, max_bit_depth, max_sample_rate)

    # 未找到指定编码时尝试降级
    actual_codec = codec
    if not specify_playlist and codec_alternative:
        for a_codec in codec_priority:
            specify_playlist = find_best_codec(parsed_m3u8, a_codec, max_bit_depth, max_sample_rate)
            if specify_playlist:
                actual_codec = a_codec
                break

    if not specify_playlist:
        raise CodecNotFoundException(f"Codec {codec} not available")

    selected_codec = specify_playlist.media[0].group_id

    # 下载选中流的 M3U8
    if download_m3u8_func:
        stream_content = await download_m3u8_func(specify_playlist.absolute_uri)
        stream = m3u8.loads(stream_content, uri=specify_playlist.absolute_uri)
    else:
        stream = m3u8.load(specify_playlist.absolute_uri)

    # 提取 SKD 密钥
    skds = [key.uri for key in stream.keys if key.uri and regex.match(r'(skd?://[^"]*)', key.uri)]
    keys = [PREFETCH_KEY]

    # 根据编码选择密钥后缀
    key_suffix = CodecKeySuffix.KeySuffixDefault
    match actual_codec:
        case Codec.ALAC:
            key_suffix = CodecKeySuffix.KeySuffixAlac
        case Codec.EC3 | Codec.AC3:
            key_suffix = CodecKeySuffix.KeySuffixAtmos
        case Codec.AAC:
            key_suffix = CodecKeySuffix.KeySuffixAAC
        case Codec.AAC_BINAURAL:
            key_suffix = CodecKeySuffix.KeySuffixAACBinaural
        case Codec.AAC_DOWNMIX:
            key_suffix = CodecKeySuffix.KeySuffixAACDownmix

    for key in skds:
        if key.endswith(key_suffix) or key.endswith(CodecKeySuffix.KeySuffixDefault):
            keys.append(key)

    # ALAC 需要补充采样率与位深
    if actual_codec == Codec.ALAC:
        sample_rate = int(specify_playlist.media[0].extras.get("sample_rate", 0))
        bit_depth = int(specify_playlist.media[0].extras.get("bit_depth", 0))
    else:
        sample_rate, bit_depth = None, None

    return M3U8Info(
        uri=stream.segment_map[0].absolute_uri,
        keys=keys,
        codec_id=selected_codec,
        bit_depth=bit_depth,
        sample_rate=sample_rate
    ), actual_codec


def extract_song(raw_song: bytes, codec: str) -> SongInfo:
    """从原始 MP4 数据提取歌曲信息与样本。"""
    tmp_dir = TemporaryDirectory()
    mp4_name = uuid.uuid4().hex
    raw_mp4 = Path(tmp_dir.name) / Path(f"{mp4_name}.mp4")

    with open(raw_mp4.absolute(), "wb") as f:
        f.write(raw_song)

    nhml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.nhml')).absolute()
    media_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.media')).absolute()

    # 使用 gpac 提取 NHML
    # 尝试在常见路径查找 gpac
    gpac_cmd = "gpac"
    if os.path.exists(r"C:\Program Files\GPAC\gpac.exe"):
        gpac_cmd = r'"C:\Program Files\GPAC\gpac.exe"'

    subprocess.run(
        f"{gpac_cmd} -i {raw_mp4.absolute()} nhmlw:pckp=true -o {nhml_name}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    xml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.xml')).absolute()

    # 使用 MP4Box 提取 ISO 信息
    mp4box_cmd = "MP4Box"
    if os.path.exists(r"C:\Program Files\GPAC\MP4Box.exe"):
        mp4box_cmd = r'"C:\Program Files\GPAC\MP4Box.exe"'

    subprocess.run(
        f"{mp4box_cmd} -diso {raw_mp4.absolute()} -out {xml_name}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    # 查找 mp4extract（Bento4 工具）
    mp4extract_cmd = "mp4extract"
    # 检查 Bento4 常见路径
    bento4_paths = [
        Path(__file__).parent.parent / "binaries" / "bento4" / "Bento4-SDK-1-6-0-641.x86_64-microsoft-win32" / "bin" / "mp4extract.exe",
        Path(r"C:\Program Files\Bento4\mp4extract.exe"),
        Path(r"C:\Bento4\bin\mp4extract.exe"),
    ]
    for bento4_path in bento4_paths:
        if bento4_path.exists():
            mp4extract_cmd = f'"{bento4_path}"'
            break

    decoder_params = None

    with open(xml_name, "r", encoding="utf-8") as f:
        info_xml = BeautifulSoup(f.read(), "xml")

    with open(nhml_name, "r", encoding="utf-8") as f:
        raw_nhml = f.read()
        nhml = BeautifulSoup(raw_nhml, "xml")

    with open(media_name, "rb") as f:
        media = BytesIO(f.read())

    # 按编码提取解码参数
    match codec:
        case Codec.ALAC:
            alac_atom_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.atom')).absolute()
            mp4extract_cmd_str = f"{mp4extract_cmd} moov/trak/mdia/minf/stbl/stsd/enca[0]/alac {raw_mp4.absolute()} {alac_atom_name}"
            print(f"[DEBUG extract_song] mp4extract command: {mp4extract_cmd_str}")
            result = subprocess.run(
                mp4extract_cmd_str,
                capture_output=True, shell=if_shell()
            )
            print(f"[DEBUG extract_song] mp4extract return code: {result.returncode}")
            if result.stderr:
                print(f"[DEBUG extract_song] mp4extract stderr: {result.stderr.decode('utf-8', errors='ignore')}")
            if not alac_atom_name.exists():
                print(f"[DEBUG extract_song] ERROR: ALAC atom file not created!")
            else:
                print(f"[DEBUG extract_song] ALAC atom file size: {alac_atom_name.stat().st_size} bytes")
            with open(alac_atom_name, "rb") as f:
                decoder_params = f.read()
            print(f"[DEBUG extract_song] decoderParams length: {len(decoder_params)} bytes")
            # 输出前 48 字节十六进制用于排查
            print(f"[DEBUG extract_song] decoderParams hex (first 48): {decoder_params[:48].hex()}")

        case Codec.AAC | Codec.AAC_DOWNMIX | Codec.AAC_BINAURAL | Codec.AAC_LEGACY:
            info_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.info')).absolute()
            if info_name.exists():
                with open(info_name, "rb") as f:
                    decoder_params = f.read()

    # 解析样本
    samples = []
    moofs = info_xml.find_all("MovieFragmentBox")
    nhnt_sample_number = 0
    nhnt_samples = {}
    params = {}

    for sample in nhml.find_all("NHNTSample"):
        nhnt_samples.update({int(sample.get("number")): sample})

    for i, moof in enumerate(moofs):
        tfhd = moof.TrackFragmentBox.TrackFragmentHeaderBox
        index = 0 if not tfhd.get("SampleDescriptionIndex") else int(tfhd.get("SampleDescriptionIndex")) - 1
        truns = moof.TrackFragmentBox.find_all("TrackRunBox")

        for trun in truns:
            for sample_number in range(int(trun.get("SampleCount"))):
                nhnt_sample_number += 1
                nhnt_sample = nhnt_samples.get(nhnt_sample_number)
                if nhnt_sample is None:
                    continue

                sample_data = media.read(int(nhnt_sample.get("dataLength")))
                duration = int(nhnt_sample.get("duration"))
                samples.append(SampleInfo(descIndex=index, data=sample_data, duration=duration))

    # 解析时间参数
    mvhd = info_xml.find("MovieHeaderBox")
    if mvhd:
        params.update({
            "CreationTime": convert_mac_timestamp_to_datetime(int(mvhd.get("CreationTime", 0))),
            "ModificationTime": convert_mac_timestamp_to_datetime(int(mvhd.get("ModificationTime", 0)))
        })

    tmp_dir.cleanup()

    return SongInfo(
        codec=codec,
        raw=raw_song,
        samples=samples,
        nhml=raw_nhml,
        decoderParams=decoder_params,
        params=params
    )


def encapsulate(song_info: SongInfo, decrypted_media: bytes, atmos_convert: bool) -> bytes:
    """封装解密后的媒体数据。"""
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    media = Path(tmp_dir.name) / Path(name).with_suffix(".media")

    with open(media.absolute(), "wb") as f:
        f.write(decrypted_media)

    song_name = Path(tmp_dir.name) / Path(name).with_suffix(get_suffix(song_info.codec, atmos_convert))

    # 查找 GPAC 工具（Windows 为 gpac.exe）
    gpac_cmd = "gpac"
    gpac_paths = [
        Path(r"C:\Program Files\GPAC\gpac.exe"),
        Path(__file__).parent.parent / "binaries" / "gpac" / "gpac.exe",
    ]
    for gpac_path in gpac_paths:
        if gpac_path.exists():
            gpac_cmd = f'"{gpac_path}"'
            break

    # 查找 Bento4 的 mp4edit 工具
    mp4edit_cmd = "mp4edit"
    bento4_paths = [
        Path(__file__).parent.parent / "binaries" / "bento4" / "Bento4-SDK-1-6-0-641.x86_64-microsoft-win32" / "bin" / "mp4edit.exe",
        Path(r"C:\Program Files\Bento4\mp4edit.exe"),
        Path(r"C:\Bento4\bin\mp4edit.exe"),
    ]
    for bento4_path in bento4_paths:
        if bento4_path.exists():
            mp4edit_cmd = f'"{bento4_path}"'
            break

    match song_info.codec:
        case Codec.ALAC:
            nhml_name = Path(tmp_dir.name) / Path(f"{name}.nhml")
            with open(nhml_name.absolute(), "w", encoding="utf-8") as f:
                nhml_xml = BeautifulSoup(song_info.nhml, features="xml")
                nhml_xml.NHNTStream["baseMediaFile"] = media.name
                f.write(str(nhml_xml))

            print(f"[DEBUG encapsulate] gpac nhmlr command...")
            gpac_result = subprocess.run(
                f'{gpac_cmd} -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}',
                capture_output=True, shell=if_shell()
            )
            print(f"[DEBUG encapsulate] gpac return code: {gpac_result.returncode}")
            if gpac_result.stderr:
                print(f"[DEBUG encapsulate] gpac stderr: {gpac_result.stderr.decode('utf-8', errors='ignore')[:500]}")
            if not song_name.exists():
                print(f"[DEBUG encapsulate] ERROR: gpac output file not created!")
            else:
                print(f"[DEBUG encapsulate] gpac output size: {song_name.stat().st_size} bytes")

            alac_params_atom_name = Path(tmp_dir.name) / Path(f"{name}.atom")
            print(f"[DEBUG encapsulate] decoderParams length: {len(song_info.decoderParams)} bytes")
            print(f"[DEBUG encapsulate] decoderParams hex (first 48): {song_info.decoderParams[:48].hex()}")
            with open(alac_params_atom_name.absolute(), "wb") as f:
                f.write(song_info.decoderParams)

            final_m4a_name = Path(tmp_dir.name) / Path(f"{name}_final.m4a")
            mp4edit_cmd_str = (
                f'{mp4edit_cmd} --insert moov/trak/mdia/minf/stbl/stsd/alac:{alac_params_atom_name.absolute()} '
                f'{song_name.absolute()} {final_m4a_name.absolute()}'
            )
            print(f"[DEBUG mp4edit] Command: {mp4edit_cmd_str}")
            print(f"[DEBUG mp4edit] shell={if_shell()}")
            result = subprocess.run(
                mp4edit_cmd_str,
                capture_output=True, shell=if_shell()
            )
            print(f"[DEBUG mp4edit] Return code: {result.returncode}")
            if result.stdout:
                print(f"[DEBUG mp4edit] stdout: {result.stdout.decode('utf-8', errors='ignore')}")
            if result.stderr:
                print(f"[DEBUG mp4edit] stderr: {result.stderr.decode('utf-8', errors='ignore')}")
            if not final_m4a_name.exists():
                print(f"[DEBUG mp4edit] ERROR: Output file not created!")
            song_name = final_m4a_name

        case Codec.EC3 | Codec.AC3:
            if not atmos_convert:
                with open(song_name.absolute(), "wb") as f:
                    f.write(decrypted_media)
            else:
                subprocess.run(
                    f'{gpac_cmd} -i {media.absolute()} -o {song_name.absolute()}',
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
                )

        case Codec.AAC_BINAURAL | Codec.AAC_DOWNMIX | Codec.AAC:
            nhml_name = Path(tmp_dir.name) / Path(f"{name}.nhml")
            info_name = Path(tmp_dir.name) / Path(f"{name}.info")

            if song_info.decoderParams:
                with open(info_name.absolute(), "wb") as f:
                    f.write(song_info.decoderParams)

            with open(nhml_name.absolute(), "w", encoding="utf-8") as f:
                nhml_xml = BeautifulSoup(song_info.nhml, features="xml")
                nhml_xml.NHNTStream["baseMediaFile"] = media.name
                if song_info.decoderParams:
                    nhml_xml.NHNTStream["specificInfoFile"] = info_name.name
                nhml_xml.NHNTStream["streamType"] = "5"
                f.write(str(nhml_xml))

            subprocess.run(
                f'{gpac_cmd} -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}',
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
            )

    # 查找 MP4Box 工具
    mp4box_cmd = "MP4Box"
    mp4box_paths = [
        Path(r"C:\Program Files\GPAC\mp4box.exe"),
        Path(__file__).parent.parent / "binaries" / "gpac" / "mp4box.exe",
    ]
    for mp4box_path in mp4box_paths:
        if mp4box_path.exists():
            mp4box_cmd = f'"{mp4box_path}"'
            break

    # 设置 M4A 标识
    if not if_raw_atmos(song_info.codec, atmos_convert):
        subprocess.run(
            f'{mp4box_cmd} -brand "M4A " -ab "M4A " -ab "mp42" {song_name.absolute()}',
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
        )

    with open(song_name.absolute(), "rb") as f:
        final_song = f.read()

    tmp_dir.cleanup()
    return final_song


def write_metadata(
    song: bytes,
    metadata: SongMetadata,
    embed_metadata: list[str],
    cover_format: str,
    params: dict[str, Any]
) -> bytes:
    """写入 M4A 元数据。"""
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")

    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    # 使用 MP4Box 设置创建/修改时间
    creation_time = params.get("CreationTime")
    modification_time = params.get("ModificationTime")

    if creation_time and modification_time:
        subprocess.run(
            ["MP4Box",
             "-time", creation_time.strftime("%d/%m/%Y-%H:%M:%S"),
             "-mtime", modification_time.strftime("%d/%m/%Y-%H:%M:%S"),
             "-keep-utc",
             "-name", f"1={metadata.title}",
             "-itags", "tool=",
             str(song_name.absolute())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    # 使用 mutagen 写入元数据
    mp4 = mutagen.mp4.Open(song_name.absolute())
    mp4.update(metadata.to_mutagen_tags(embed_metadata))
    mp4.save()

    with open(song_name.absolute(), "rb") as f:
        embed_song = f.read()

    tmp_dir.cleanup()
    return embed_song


def fix_encapsulate(song: bytes) -> bytes:
    """使用 FFmpeg 修复 M4A 封装问题。"""
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    new_song_name = Path(tmp_dir.name) / Path(f"{name}_fixed.m4a")

    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    print(f"[DEBUG fix_encapsulate] Input file size: {len(song)} bytes")

    ffmpeg_cmd = (
        f"ffmpeg -y -i {song_name.absolute()} -fflags +bitexact -map_metadata 0 "
        f"-c:a copy -c:v copy {new_song_name.absolute()}"
    )
    print(f"[DEBUG fix_encapsulate] FFmpeg command: {ffmpeg_cmd}")

    result = subprocess.run(
        ffmpeg_cmd,
        capture_output=True, shell=if_shell()
    )
    print(f"[DEBUG fix_encapsulate] FFmpeg return code: {result.returncode}")
    if result.stderr:
        stderr_text = result.stderr.decode('utf-8', errors='ignore')
        # 检查 ALAC 相关错误
        if 'alac' in stderr_text.lower() or 'invalid' in stderr_text.lower():
            print(f"[DEBUG fix_encapsulate] FFmpeg stderr (ALAC related): {stderr_text[:1000]}")

    if not new_song_name.exists():
        print(f"[DEBUG fix_encapsulate] ERROR: Output file not created!")
        # FFmpeg 失败时返回原始数据
        tmp_dir.cleanup()
        return song

    print(f"[DEBUG fix_encapsulate] Output file size: {new_song_name.stat().st_size} bytes")

    with open(new_song_name.absolute(), "rb") as f:
        encapsulated_song = f.read()

    tmp_dir.cleanup()
    return encapsulated_song


def fix_esds_box(raw_song: bytes, song: bytes) -> bytes:
    """修复 AAC 文件中的 ESDS box。"""
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    esds_name = Path(tmp_dir.name) / Path(f"{name}.atom")
    raw_song_name = Path(tmp_dir.name) / Path(f"{name}_raw.m4a")
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    final_song_name = Path(tmp_dir.name) / Path(f"{name}_final.m4a")

    with open(raw_song_name.absolute(), "wb") as f:
        f.write(raw_song)
    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    # 查找 mp4extract 与 mp4edit（Bento4 工具）
    mp4extract_cmd = "mp4extract"
    mp4edit_cmd = "mp4edit"
    bento4_bin_paths = [
        Path(__file__).parent.parent / "binaries" / "bento4" / "Bento4-SDK-1-6-0-641.x86_64-microsoft-win32" / "bin",
        Path(r"C:\Program Files\Bento4"),
        Path(r"C:\Bento4\bin"),
    ]
    for bento4_bin in bento4_bin_paths:
        mp4extract_path = bento4_bin / "mp4extract.exe"
        mp4edit_path = bento4_bin / "mp4edit.exe"
        if mp4extract_path.exists():
            mp4extract_cmd = f'"{mp4extract_path}"'
            mp4edit_cmd = f'"{mp4edit_path}"'
            break

    # 提取原始 ESDS box
    subprocess.run(
        f"{mp4extract_cmd} moov/trak/mdia/minf/stbl/stsd/enca[0]/esds {raw_song_name.absolute()} {esds_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    # 替换处理后文件中的 ESDS box
    subprocess.run(
        f"{mp4edit_cmd} --replace moov/trak/mdia/minf/stbl/stsd/mp4a/esds:{esds_name.absolute()} "
        f"{song_name.absolute()} {final_song_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    with open(final_song_name.absolute(), "rb") as f:
        final_song = f.read()

    tmp_dir.cleanup()
    return final_song


def check_song_integrity(song: bytes) -> bool:
    """使用 FFmpeg 校验歌曲文件完整性。"""
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")

    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    # Unix 使用 /dev/null，Windows 使用 NUL
    null_device = "NUL" if not if_shell() else "/dev/null"

    output = subprocess.run(
        f"ffmpeg -y -v error -i {song_name.absolute()} -c:a pcm_s16le -f null {null_device}",
        capture_output=True, shell=if_shell()
    )

    tmp_dir.cleanup()
    return not bool(output.stderr)
