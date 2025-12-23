"""
Apple Music Data Models

"""

from .song_data import SongData
from .album_meta import AlbumMeta, Tracks
from .album_tracks import AlbumTracks
from .artist_info import ArtistInfo
from .artist_albums import ArtistAlbums
from .artist_songs import ArtistSongs
from .playlist_info import PlaylistInfo
from .playlist_tracks import PlaylistTracks
from .song_lyrics import SongLyrics
from .tracks_meta import TracksMeta

__all__ = [
    "SongData",
    "AlbumMeta",
    "Tracks",
    "AlbumTracks",
    "ArtistInfo",
    "ArtistAlbums",
    "ArtistSongs",
    "PlaylistInfo",
    "PlaylistTracks",
    "SongLyrics",
    "TracksMeta",
]
