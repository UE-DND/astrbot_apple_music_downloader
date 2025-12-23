"""歌曲数据模型。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Artwork(BaseModel):
    width: Optional[int] = None
    url: Optional[str] = None
    height: Optional[int] = None
    textColor3: Optional[str] = None
    textColor2: Optional[str] = None
    textColor4: Optional[str] = None
    textColor1: Optional[str] = None
    bgColor: Optional[str] = None
    hasP3: Optional[bool] = None


class PlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None


class Preview(BaseModel):
    url: Optional[str] = None


class ExtendedAssetUrls(BaseModel):
    plus: Optional[str] = None
    lightweight: Optional[str] = None
    superLightweight: Optional[str] = None
    lightweightPlus: Optional[str] = None
    enhancedHls: Optional[str] = None


class SongAttributes(BaseModel):
    hasTimeSyncedLyrics: Optional[bool] = None
    albumName: Optional[str] = None
    genreNames: List[Optional[str]] = None
    trackNumber: Optional[int] = None
    durationInMillis: Optional[int] = None
    releaseDate: Optional[str] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Artwork
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    url: Optional[str] = None
    playParams: Optional[PlayParams] = None
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    hasLyrics: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    name: Optional[str] = None
    previews: List[Preview]
    artistName: Optional[str] = None
    extendedAssetUrls: Optional[ExtendedAssetUrls] = None
    contentRating: Optional[str] = None


class AlbumArtwork(BaseModel):
    width: Optional[int] = None
    url: Optional[str] = None
    height: Optional[int] = None
    textColor3: Optional[str] = None
    textColor2: Optional[str] = None
    textColor4: Optional[str] = None
    textColor1: Optional[str] = None
    bgColor: Optional[str] = None
    hasP3: Optional[bool] = None


class AlbumPlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None


class AlbumAttributes(BaseModel):
    copyright: Optional[str] = None
    genreNames: List[Optional[str]] = None
    releaseDate: Optional[str] = None
    isMasteredForItunes: Optional[bool] = None
    upc: Optional[str] = None
    artwork: AlbumArtwork
    url: Optional[str] = None
    playParams: Optional[AlbumPlayParams] = None
    recordLabel: Optional[str] = None
    isCompilation: Optional[bool] = None
    trackCount: Optional[int] = None
    isPrerelease: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    isSingle: Optional[bool] = None
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: Optional[bool] = None


class AlbumDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: AlbumAttributes


class Albums(BaseModel):
    href: Optional[str] = None
    data: List[AlbumDatum]


class ArtistDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None


class Artists(BaseModel):
    href: Optional[str] = None
    data: List[ArtistDatum]


class SongRelationships(BaseModel):
    albums: Albums
    artists: Artists


class SongDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: SongAttributes
    relationships: SongRelationships


class SongData(BaseModel):
    data: List[SongDatum]
