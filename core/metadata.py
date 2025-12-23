"""
歌曲元数据处理器。
用于解析与生成 MP4 标签。
"""

from typing import Optional, Dict, List

from mutagen.mp4 import MP4Cover
from pydantic import BaseModel

from .models.song_data import SongDatum
from .models.album_meta import AlbumMeta, Tracks


# 不写入 MP4 标签的字段
NOT_INCLUDED_FIELD = [
    "playlistIndex",
    "bit_depth",
    "sample_rate",
    "sample_rate_kHz",
    "track_total",
    "disk_total",
    "cover_url",
]

# 元数据字段与 MP4 标签原子映射
TAG_MAPPING = {
    "song_id": "cnID",  # iTunes 目录 ID
    "title": "©nam",  # MP4 标题
    "artist": "©ART",  # MP4 艺术家
    "album_id": "plID",  # iTunes 专辑 ID
    "album_artist": "aART",  # MP4 专辑艺术家
    "album": "©alb",  # MP4 专辑
    "album_created": "©day",  # MP4 年份
    "composer": "©wrt",  # MP4 作曲
    "genre": "©gen",  # MP4 流派
    "created": "purd",  # MP4 购买时间
    "track": "©trk",  # MP4 曲名
    "tracknum": "trkn",  # MP4 曲目编号/总数
    "disk": "disk",  # MP4 碟号
    "lyrics": "©lyr",  # MP4 非同步歌词
    "cover": "covr",  # MP4 封面原子
    "copyright": "cprt",  # MP4 版权
    "record_company": "©pub",  # MP4 唱片公司
    "upc": "----:com.apple.iTunes:BARCODE",  # MP4 UPC 条码
    "isrc": "----:com.apple.iTunes:ISRC",  # MP4 ISRC
    "rtng": "rtng",  # MP4 分级
    "artist_id": "atID",  # iTunes 艺术家 ID
}


def count_total_track_and_disc(tracks: Tracks) -> tuple[int, dict[int, int]]:
    """统计专辑碟数与每碟曲目数。"""
    disc_count = tracks.data[-1].attributes.discNumber if tracks.data else 1
    track_count: dict[int, int] = {}

    for track in tracks.data:
        disc_num = track.attributes.discNumber or 1
        track_num = track.attributes.trackNumber or 0
        if track_count.get(disc_num, 0) < track_num:
            track_count[disc_num] = track_num

    return disc_count, track_count


class SongMetadata(BaseModel):
    """歌曲元数据容器。"""

    song_id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album_id: Optional[str] = None
    album_artist: Optional[str] = None
    album: Optional[str] = None
    album_created: Optional[str] = None
    composer: Optional[str] = None
    genre: Optional[List[str]] = None
    created: Optional[str] = None
    track: Optional[str] = None
    tracknum: Optional[int] = None
    track_total: Optional[Dict[int, int]] = None
    disk: Optional[int] = None
    disk_total: Optional[int] = None
    lyrics: Optional[str] = None
    cover: bytes = None
    cover_url: Optional[str] = None
    copyright: Optional[str] = None
    record_company: Optional[str] = None
    upc: Optional[str] = None
    isrc: Optional[str] = None
    rtng: Optional[int] = None
    playlist_index: Optional[int] = None
    bit_depth: Optional[int] = None
    sample_rate: Optional[int] = None
    sample_rate_kHz: Optional[str] = None
    artist_id: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}

    def to_mutagen_tags(self, embed_metadata: list[str]) -> dict:
        """转换为 Mutagen MP4 标签格式。"""
        tags = {}

        for key, value in self.model_dump().items():
            if not value and key != "rtng":
                continue

            if key not in embed_metadata:
                continue

            if key in NOT_INCLUDED_FIELD:
                continue

            # 处理特殊字段
            if key == "lyrics":
                tags[TAG_MAPPING[key]] = value
                continue

            if key == "tracknum":
                disk_num = self.disk or 1
                total = self.track_total.get(disk_num, 1) if self.track_total else 1
                tags[TAG_MAPPING[key]] = ((value, total),)
                continue

            if key == "disk":
                tags[TAG_MAPPING[key]] = ((value, self.disk_total or 1),)
                continue

            if key == "cover":
                tags[TAG_MAPPING[key]] = (MP4Cover(value),)
                continue

            if key == "upc":
                tags[TAG_MAPPING[key]] = (value.encode(),)
                continue

            if key == "isrc":
                tags[TAG_MAPPING[key]] = (value.encode(),)
                continue

            if key == "genre":
                tags[TAG_MAPPING[key]] = value
                continue

            if key == "rtng":
                tags[TAG_MAPPING[key]] = (value,)
                continue

            if key == "song_id":
                tags[TAG_MAPPING[key]] = (int(value),)
                continue

            if key == "album_id":
                tags[TAG_MAPPING[key]] = (int(value),)
                continue

            if key == "artist_id":
                tags[TAG_MAPPING[key]] = (int(value),)
                continue

            tags[TAG_MAPPING[key]] = str(value)

        return tags

    @classmethod
    def parse_from_song_data(cls, song_data: SongDatum) -> "SongMetadata":
        """从歌曲数据解析元数据。"""
        album_data = song_data.relationships.albums.data[0] if song_data.relationships.albums.data else None
        artist_data = song_data.relationships.artists.data[0] if song_data.relationships.artists.data else None

        return cls(
            title=song_data.attributes.name,
            artist=song_data.attributes.artistName,
            album_artist=album_data.attributes.artistName if album_data else None,
            album=song_data.attributes.albumName,
            composer=song_data.attributes.composerName,
            genre=song_data.attributes.genreNames,
            created=song_data.attributes.releaseDate,
            track=song_data.attributes.name,
            tracknum=song_data.attributes.trackNumber,
            disk=song_data.attributes.discNumber,
            lyrics="",
            cover_url=song_data.attributes.artwork.url if song_data.attributes.artwork else None,
            copyright=album_data.attributes.copyright if album_data else None,
            record_company=album_data.attributes.recordLabel if album_data else None,
            upc=album_data.attributes.upc if album_data else None,
            isrc=song_data.attributes.isrc,
            album_created=album_data.attributes.releaseDate if album_data else None,
            rtng=cls._rating(song_data.attributes.contentRating),
            song_id=song_data.id,
            album_id=album_data.id if album_data else None,
            artist_id=artist_data.id if artist_data else None,
        )

    def parse_from_album_data(self, album_data: AlbumMeta):
        """根据专辑数据更新曲目统计。"""
        if album_data.data[0].relationships and album_data.data[0].relationships.tracks:
            self.disk_total, self.track_total = count_total_track_and_disc(
                album_data.data[0].relationships.tracks
            )

    @staticmethod
    def _rating(content_rating: Optional[str]) -> int:
        """将内容分级字符串转换为数值。"""
        if not content_rating:
            return 0
        if content_rating == "explicit":
            return 1
        if content_rating == "clean":
            return 2
        return 0

    def set_lyrics(self, lyrics: str):
        """设置歌词字段。"""
        self.lyrics = lyrics

    def set_cover(self, cover: bytes):
        """设置封面数据。"""
        self.cover = cover

    def set_playlist_index(self, index: int):
        """设置歌单序号。"""
        self.playlist_index = index

    def set_bit_depth_and_sample_rate(self, bit_depth: int, sample_rate: int):
        """设置音质信息。"""
        self.bit_depth = bit_depth
        self.sample_rate = sample_rate
        self.sample_rate_kHz = str(sample_rate / 1000)
