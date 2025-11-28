"""
Apple Music URL Parser


Parses Apple Music URLs to extract type, storefront, and ID.
"""

from urllib.parse import urlparse, parse_qs
from typing import Optional

import regex
from pydantic import BaseModel


class URLType:
    """URL type constants."""
    Song = "song"
    Album = "album"
    Playlist = "playlist"
    Artist = "artist"


class AppleMusicURL(BaseModel):
    """
    Base class for parsed Apple Music URLs.

    Attributes:
        url: The original URL
        storefront: The region/storefront code (e.g., 'cn', 'us')
        type: The URL type (song, album, playlist, artist)
        id: The resource ID
    """
    url: str
    storefront: str
    type: str
    id: str

    @classmethod
    def parse_url(cls, url: str) -> Optional["AppleMusicURL"]:
        """
        Parse an Apple Music URL into a typed object.

        Args:
            url: The Apple Music URL to parse

        Returns:
            A Song, Album, Playlist, or Artist object, or None if invalid

        Examples:
            >>> AppleMusicURL.parse_url("https://music.apple.com/cn/song/title/123456")
            Song(url='...', storefront='cn', type='song', id='123456')

            >>> AppleMusicURL.parse_url("https://music.apple.com/us/album/title/123?i=456")
            Song(url='...', storefront='us', type='song', id='456')

            >>> AppleMusicURL.parse_url("https://music.apple.com/jp/album/title/123")
            Album(url='...', storefront='jp', type='album', id='123')
        """
        # Validate URL format
        if not regex.match(r"https://music.apple.com/(.{2})/(song|album|playlist|artist).*/(pl.*|\d*)", url):
            return None

        parsed_url = urlparse(url)
        paths = parsed_url.path.split("/")

        # Extract storefront (region code)
        storefront = paths[1]

        # Extract URL type
        url_type = paths[2]

        match url_type:
            case URLType.Song:
                url_id = paths[-1]
                return Song(url=url, storefront=storefront, id=url_id, type=URLType.Song)

            case URLType.Album:
                # Check for ?i= parameter (single song from album)
                if not parsed_url.query:
                    url_id = paths[-1]
                    return Album(url=url, storefront=storefront, id=url_id, type=URLType.Album)
                else:
                    url_query = parse_qs(parsed_url.query)
                    if url_query.get("i"):
                        # This is actually a song URL (album URL with track selection)
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
        """
        Check if a URL is a valid Apple Music URL.

        Args:
            url: The URL to validate

        Returns:
            True if valid, False otherwise
        """
        return cls.parse_url(url) is not None


class Song(AppleMusicURL):
    """Represents a parsed Apple Music song URL."""
    pass


class Album(AppleMusicURL):
    """Represents a parsed Apple Music album URL."""
    pass


class Playlist(AppleMusicURL):
    """Represents a parsed Apple Music playlist URL."""
    pass


class Artist(AppleMusicURL):
    """Represents a parsed Apple Music artist URL."""
    pass
