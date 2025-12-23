"""
Apple Music URL 解析器。
用于提取类型、地区与资源 ID。
"""

from urllib.parse import urlparse, parse_qs
from typing import Optional

import regex
from pydantic import BaseModel


class URLType:
    """链接类型常量。"""
    Song = "song"
    Album = "album"
    Playlist = "playlist"
    Artist = "artist"


class AppleMusicURL(BaseModel):
    """解析后的 Apple Music URL 基类。"""
    url: str
    storefront: str
    type: str
    id: str

    @classmethod
    def parse_url(cls, url: str) -> Optional["AppleMusicURL"]:
        """解析 Apple Music URL 并返回对应对象。"""
        if not regex.match(r"https://music.apple.com/(.{2})/(song|album|playlist|artist).*/(pl.*|\d*)", url):
            return None

        parsed_url = urlparse(url)
        paths = parsed_url.path.split("/")

        storefront = paths[1]

        url_type = paths[2]

        match url_type:
            case URLType.Song:
                url_id = paths[-1]
                return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)

            case URLType.Album:
                if not parsed_url.query:
                    url_id = paths[-1]
                    return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)
                else:
                    url_query = parse_qs(parsed_url.query)
                    if url_query.get("i"):
                        url_id = url_query.get("i")[0]
                        return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)
                    else:
                        url_id = paths[-1]
                        return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)

            case URLType.Artist:
                url_id = paths[-1]
                return Artist(url=url, storefront=storefront, id=url_id, type=URLType.Artist)

            case URLType.Playlist:
                url_id = paths[-1]
                return Playlist(url=url, storefront=storefront, id=url_id, type=URLType.Playlist)

        return None

    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        """检查 URL 是否为有效的 Apple Music 链接。"""
        return cls.parse_url(url) is not None


class Song(AppleMusicURL):
    """解析后的 Apple Music 单曲链接。"""
    pass


class Album(AppleMusicURL):
    """解析后的 Apple Music 专辑链接。"""
    pass


class Playlist(AppleMusicURL):
    """解析后的 Apple Music 歌单链接。"""
    pass


class Artist(AppleMusicURL):
    """解析后的 Apple Music 艺人链接。"""
    pass
