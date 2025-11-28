"""
MP4 Processing Module


Handles M3U8 extraction, audio extraction, encapsulation, and metadata writing.
"""

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
    """Raised when the requested codec is not available."""
    pass


async def get_available_codecs(m3u8_content: str, m3u8_url: str) -> Tuple[list[str], list[str]]:
    """
    Get available codecs from M3U8 playlist.

    Args:
        m3u8_content: M3U8 playlist content
        m3u8_url: M3U8 URL for resolving relative URIs

    Returns:
        Tuple of (codecs, codec_ids)
    """
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
    """
    Extract media information from M3U8 playlist.

    Args:
        m3u8_content: M3U8 playlist content
        m3u8_url: M3U8 URL
        codec: Requested codec
        codec_priority: Priority list for codec alternatives
        codec_alternative: Whether to try alternative codecs
        max_bit_depth: Maximum bit depth for ALAC
        max_sample_rate: Maximum sample rate for ALAC
        download_m3u8_func: Async function to download M3U8 content

    Returns:
        Tuple of (M3U8Info, actual_codec)

    Raises:
        CodecNotFoundException: If no suitable codec found
    """
    parsed_m3u8 = m3u8.loads(m3u8_content, uri=m3u8_url)
    specify_playlist = find_best_codec(parsed_m3u8, codec, max_bit_depth, max_sample_rate)

    # Try alternative codecs if requested codec not found
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

    # Download the specific stream M3U8
    if download_m3u8_func:
        stream_content = await download_m3u8_func(specify_playlist.absolute_uri)
        stream = m3u8.loads(stream_content, uri=specify_playlist.absolute_uri)
    else:
        stream = m3u8.load(specify_playlist.absolute_uri)

    # Extract SKD keys
    skds = [key.uri for key in stream.keys if key.uri and regex.match(r'(skd?://[^"]*)', key.uri)]
    keys = [PREFETCH_KEY]

    # Determine key suffix based on codec
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

    # Get sample rate and bit depth for ALAC
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
    """
    Extract song information and samples from raw MP4 data.

    Args:
        raw_song: Raw MP4 bytes
        codec: Audio codec

    Returns:
        SongInfo with samples and decoder params
    """
    tmp_dir = TemporaryDirectory()
    mp4_name = uuid.uuid4().hex
    raw_mp4 = Path(tmp_dir.name) / Path(f"{mp4_name}.mp4")

    with open(raw_mp4.absolute(), "wb") as f:
        f.write(raw_song)

    nhml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.nhml')).absolute()
    media_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.media')).absolute()

    # Extract NHML using gpac
    subprocess.run(
        f"gpac -i {raw_mp4.absolute()} nhmlw:pckp=true -o {nhml_name}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    xml_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.xml')).absolute()

    # Extract ISO info using MP4Box
    subprocess.run(
        f"MP4Box -diso {raw_mp4.absolute()} -out {xml_name}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    decoder_params = None

    with open(xml_name, "r", encoding="utf-8") as f:
        info_xml = BeautifulSoup(f.read(), "xml")

    with open(nhml_name, "r", encoding="utf-8") as f:
        raw_nhml = f.read()
        nhml = BeautifulSoup(raw_nhml, "xml")

    with open(media_name, "rb") as f:
        media = BytesIO(f.read())

    # Extract decoder params based on codec
    match codec:
        case Codec.ALAC:
            alac_atom_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.atom')).absolute()
            subprocess.run(
                f"mp4extract moov/trak/mdia/minf/stbl/stsd/enca[0]/alac {raw_mp4.absolute()} {alac_atom_name}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
            )
            with open(alac_atom_name, "rb") as f:
                decoder_params = f.read()

        case Codec.AAC | Codec.AAC_DOWNMIX | Codec.AAC_BINAURAL | Codec.AAC_LEGACY:
            info_name = (Path(tmp_dir.name) / Path(mp4_name).with_suffix('.info')).absolute()
            if info_name.exists():
                with open(info_name, "rb") as f:
                    decoder_params = f.read()

    # Parse samples
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

    # Extract timing params
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
    """
    Encapsulate decrypted media into proper container format.

    Args:
        song_info: Song information with codec and params
        decrypted_media: Decrypted audio data
        atmos_convert: Whether to convert Atmos to M4A

    Returns:
        Encapsulated audio bytes
    """
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    media = Path(tmp_dir.name) / Path(name).with_suffix(".media")

    with open(media.absolute(), "wb") as f:
        f.write(decrypted_media)

    song_name = Path(tmp_dir.name) / Path(name).with_suffix(get_suffix(song_info.codec, atmos_convert))

    match song_info.codec:
        case Codec.ALAC:
            nhml_name = Path(tmp_dir.name) / Path(f"{name}.nhml")
            with open(nhml_name.absolute(), "w", encoding="utf-8") as f:
                nhml_xml = BeautifulSoup(song_info.nhml, features="xml")
                nhml_xml.NHNTStream["baseMediaFile"] = media.name
                f.write(str(nhml_xml))

            subprocess.run(
                f"gpac -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
            )

            alac_params_atom_name = Path(tmp_dir.name) / Path(f"{name}.atom")
            with open(alac_params_atom_name.absolute(), "wb") as f:
                f.write(song_info.decoderParams)

            final_m4a_name = Path(tmp_dir.name) / Path(f"{name}_final.m4a")
            subprocess.run(
                f"mp4edit --insert moov/trak/mdia/minf/stbl/stsd/alac:{alac_params_atom_name.absolute()} "
                f"{song_name.absolute()} {final_m4a_name.absolute()}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
            )
            song_name = final_m4a_name

        case Codec.EC3 | Codec.AC3:
            if not atmos_convert:
                with open(song_name.absolute(), "wb") as f:
                    f.write(decrypted_media)
            else:
                subprocess.run(
                    f"gpac -i {media.absolute()} -o {song_name.absolute()}",
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
                f"gpac -i {nhml_name.absolute()} nhmlr -o {song_name.absolute()}",
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
            )

    # Set M4A brand
    if not if_raw_atmos(song_info.codec, atmos_convert):
        subprocess.run(
            f'MP4Box -brand "M4A " -ab "M4A " -ab "mp42" {song_name.absolute()}',
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
    """
    Write metadata tags to M4A file.

    Args:
        song: Audio bytes
        metadata: Song metadata
        embed_metadata: List of fields to embed
        cover_format: Cover image format
        params: Additional params (CreationTime, ModificationTime)

    Returns:
        Audio bytes with embedded metadata
    """
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")

    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    # Set creation/modification time using MP4Box
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

    # Write metadata using mutagen
    mp4 = mutagen.mp4.Open(song_name.absolute())
    mp4.update(metadata.to_mutagen_tags(embed_metadata))
    mp4.save()

    with open(song_name.absolute(), "rb") as f:
        embed_song = f.read()

    tmp_dir.cleanup()
    return embed_song


def fix_encapsulate(song: bytes) -> bytes:
    """
    Fix M4A encapsulation issues using FFmpeg.

    Some M4A files encapsulated by MP4Box/GPAC have metadata issues
    that prevent proper playback on some devices.

    Args:
        song: Audio bytes

    Returns:
        Fixed audio bytes
    """
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")
    new_song_name = Path(tmp_dir.name) / Path(f"{name}_fixed.m4a")

    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    subprocess.run(
        f"ffmpeg -y -i {song_name.absolute()} -fflags +bitexact -map_metadata 0 "
        f"-c:a copy -c:v copy {new_song_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    with open(new_song_name.absolute(), "rb") as f:
        encapsulated_song = f.read()

    tmp_dir.cleanup()
    return encapsulated_song


def fix_esds_box(raw_song: bytes, song: bytes) -> bytes:
    """
    Fix ESDS box in AAC files after FFmpeg processing.

    FFmpeg may overwrite maxBitrate in DecoderConfigDescriptor.
    This function restores the original ESDS box.

    Args:
        raw_song: Original encrypted song bytes
        song: Processed song bytes

    Returns:
        Fixed audio bytes
    """
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

    # Extract original ESDS box
    subprocess.run(
        f"mp4extract moov/trak/mdia/minf/stbl/stsd/enca[0]/esds {raw_song_name.absolute()} {esds_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    # Replace ESDS box in processed file
    subprocess.run(
        f"mp4edit --replace moov/trak/mdia/minf/stbl/stsd/mp4a/esds:{esds_name.absolute()} "
        f"{song_name.absolute()} {final_song_name.absolute()}",
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=if_shell()
    )

    with open(final_song_name.absolute(), "rb") as f:
        final_song = f.read()

    tmp_dir.cleanup()
    return final_song


def check_song_integrity(song: bytes) -> bool:
    """
    Check if the song file is valid using FFmpeg.

    Args:
        song: Audio bytes

    Returns:
        True if file is valid
    """
    tmp_dir = TemporaryDirectory()
    name = uuid.uuid4().hex
    song_name = Path(tmp_dir.name) / Path(f"{name}.m4a")

    with open(song_name.absolute(), "wb") as f:
        f.write(song)

    # Use /dev/null on Unix, NUL on Windows
    null_device = "NUL" if not if_shell() else "/dev/null"

    output = subprocess.run(
        f"ffmpeg -y -v error -i {song_name.absolute()} -c:a pcm_s16le -f null {null_device}",
        capture_output=True, shell=if_shell()
    )

    tmp_dir.cleanup()
    return not bool(output.stderr)
