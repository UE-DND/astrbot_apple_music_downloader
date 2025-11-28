"""
Apple Music Downloader Core Types

"""

from typing import Optional, Any, Callable, Awaitable

from pydantic import BaseModel


# Default constants
DEFAULT_ID = "0"
PREFETCH_KEY = "skd://itunes.apple.com/P000000000/s1/e1"


class ParentDoneHandler:
    """Handler for tracking parent task completion."""
    count: int
    callback: Callable[[], Awaitable[None]]

    def __init__(self, count: int, callback: Callable[[], Awaitable[None]]):
        self.count = count
        self.callback = callback

    async def try_done(self):
        self.count -= 1
        if self.count == 0:
            await self.callback()


class SampleInfo(BaseModel):
    """Audio sample information."""
    data: bytes
    duration: int
    descIndex: int


class SongInfo(BaseModel):
    """Song information with audio data."""
    codec: str
    raw: bytes
    samples: list[SampleInfo]
    nhml: str
    decoderParams: Optional[bytes] = None
    params: dict[str, Any]


class M3U8Info(BaseModel):
    """M3U8 stream information."""
    uri: str
    keys: list[str]
    codec_id: str
    bit_depth: Optional[int] = None
    sample_rate: Optional[int] = None


class Codec:
    """Supported audio codec identifiers."""
    ALAC = "alac"
    EC3 = "ec3"
    AC3 = "ac3"
    AAC_BINAURAL = "aac-binaural"
    AAC_DOWNMIX = "aac-downmix"
    AAC = "aac"
    AAC_LEGACY = "aac-legacy"


class CodecKeySuffix:
    """Key suffixes for different codecs."""
    KeySuffixAtmos = "c24"
    KeySuffixAlac = "c23"
    KeySuffixAAC = "c22"
    KeySuffixAACDownmix = "c24"
    KeySuffixAACBinaural = "c24"
    KeySuffixDefault = "c6"


class CodecRegex:
    """Regex patterns for codec identification."""
    RegexCodecAtmos = r"audio-(atmos|ec3)-\d{4}$"
    RegexCodecAC3 = r"audio-ac3-\d{3}$"
    RegexCodecAlac = r"audio-alac-stereo-\d{5,6}-\d{2}$"
    RegexCodecBinaural = r"audio-stereo-\d{3}-binaural$"
    RegexCodecDownmix = r"audio-stereo-\d{3}-downmix$"
    RegexCodecAAC = r"audio-stereo-\d{3}$"

    @classmethod
    def get_pattern_by_codec(cls, codec: str) -> Optional[str]:
        """Get regex pattern for a specific codec."""
        codec_pattern_mapping = {
            Codec.ALAC: cls.RegexCodecAlac,
            Codec.EC3: cls.RegexCodecAtmos,
            Codec.AAC_DOWNMIX: cls.RegexCodecDownmix,
            Codec.AAC_BINAURAL: cls.RegexCodecBinaural,
            Codec.AAC: cls.RegexCodecAAC,
            Codec.AAC_LEGACY: cls.RegexCodecAAC,
            Codec.AC3: cls.RegexCodecAC3,
        }
        return codec_pattern_mapping.get(codec)
