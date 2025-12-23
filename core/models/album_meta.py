"""专辑元数据模型。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


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


class AlbumAttributes(BaseModel):
    copyright: Optional[str] = None
    genreNames: List[Optional[str]] = None
    releaseDate: Optional[str] = None
    upc: Optional[str] = None
    isMasteredForItunes: Optional[bool] = None
    artwork: Optional[Artwork]
    url: Optional[str] = None
    playParams: Optional[PlayParams] = None
    recordLabel: Optional[str] = None
    isCompilation: Optional[bool] = None
    trackCount: Optional[int] = None
    isPrerelease: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    isSingle: Optional[bool] = None
    name: Optional[str] = None
    artistName: Optional[str] = None
    isComplete: Optional[bool] = None
    contentRating: Optional[str] = None


class TrackArtwork(BaseModel):
    width: Optional[int] = None
    url: Optional[str] = None
    height: Optional[int] = None
    textColor3: Optional[str] = None
    textColor2: Optional[str] = None
    textColor4: Optional[str] = None
    textColor1: Optional[str] = None
    bgColor: Optional[str] = None
    hasP3: Optional[bool] = None


class TrackPlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None


class Preview(BaseModel):
    url: Optional[str] = None


class TrackAttributes(BaseModel):
    hasTimeSyncedLyrics: Optional[bool] = None
    albumName: Optional[str] = None
    genreNames: List[Optional[str]] = None
    trackNumber: Optional[int] = None
    durationInMillis: Optional[int] = None
    releaseDate: Optional[str] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: Optional[TrackArtwork] = None
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    playParams: Optional[TrackPlayParams] = None
    url: Optional[str] = None
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    hasLyrics: Optional[bool] = None
    audioTraits: List[Optional[str]] = None
    name: Optional[str] = None
    previews: List[Preview]
    artistName: Optional[str] = None


class ArtistAttributes(BaseModel):
    name: Optional[str] = None


class ArtistDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[ArtistAttributes] = None


class TrackArtists(BaseModel):
    href: Optional[str] = None
    data: Optional[List[ArtistDatum]] = None


class TrackRelationships(BaseModel):
    artists: Optional[TrackArtists] = None


class TrackDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[TrackAttributes]
    relationships: Optional[TrackRelationships] = None


class Tracks(BaseModel):
    href: Optional[str] = None
    next: Optional[str] = None
    data: List[TrackDatum] = None


class AlbumArtistAttributes(BaseModel):
    name: Optional[str] = None


class AlbumArtistDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[AlbumArtistAttributes] = None


class AlbumArtists(BaseModel):
    href: Optional[str] = None
    data: List[AlbumArtistDatum] = None


class RecordLabels(BaseModel):
    href: Optional[str] = None
    data: Optional[list] = None


class AlbumRelationships(BaseModel):
    tracks: Optional[Tracks] = None
    artists: Optional[AlbumArtists] = None
    record_labels: Optional[RecordLabels] = Field(default=None, alias='record-labels')


class ContentVersion(BaseModel):
    MZ_INDEXER: Optional[int] = None
    RTCI: Optional[int] = None


class Meta(BaseModel):
    contentVersion: Optional[ContentVersion] = None


class AlbumDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: Optional[AlbumAttributes] = None
    relationships: Optional[AlbumRelationships] = None
    meta: Optional[Meta] = None


class AlbumMeta(BaseModel):
    data: List[AlbumDatum]
