"""
Apple Music Downloader 核心类型。
"""

from typing import Optional, Any, Callable, Awaitable

from pydantic import BaseModel


DEFAULT_ID = "0"
PREFETCH_KEY = "skd://itunes.apple.com/P000000000/s1/e1"


class ParentDoneHandler:
    """父任务完成计数器回调处理器。"""
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
    """音频采样信息。"""
    data: bytes
    duration: int
    descIndex: int


class SongInfo(BaseModel):
    """包含音频数据的歌曲信息。"""
    codec: str
    raw: bytes
    samples: list[SampleInfo]
    nhml: str
    decoderParams: Optional[bytes] = None
    params: dict[str, Any]


class M3U8Info(BaseModel):
    """流信息（M3U8）。"""
    uri: str
    keys: list[str]
    codec_id: str
    bit_depth: Optional[int] = None
    sample_rate: Optional[int] = None


class Codec:
    """支持的音频编码标识。"""
    ALAC = "alac"
    EC3 = "ec3"
    AC3 = "ac3"
    AAC_BINAURAL = "aac-binaural"
    AAC_DOWNMIX = "aac-downmix"
    AAC = "aac"
    AAC_LEGACY = "aac-legacy"


class CodecKeySuffix:
    """不同编码的密钥后缀。"""
    KeySuffixAtmos = "c24"
    KeySuffixAlac = "c23"
    KeySuffixAAC = "c22"
    KeySuffixAACDownmix = "c24"
    KeySuffixAACBinaural = "c24"
    KeySuffixDefault = "c6"


class CodecRegex:
    """编码识别正则。"""
    RegexCodecAtmos = r"audio-(atmos|ec3)-\d{4}$"
    RegexCodecAC3 = r"audio-ac3-\d{3}$"
    RegexCodecAlac = r"audio-alac-stereo-\d{5,6}-\d{2}$"
    RegexCodecBinaural = r"audio-stereo-\d{3}-binaural$"
    RegexCodecDownmix = r"audio-stereo-\d{3}-downmix$"
    RegexCodecAAC = r"audio-stereo-\d{3}$"

    @classmethod
    def get_pattern_by_codec(cls, codec: str) -> Optional[str]:
        """获取指定编码的正则模式。"""
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
