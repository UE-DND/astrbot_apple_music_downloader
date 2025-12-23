"""歌单信息模型。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class Description(BaseModel):
    standard: Optional[str] = None


class Artwork(BaseModel):
    width: Optional[int] = None
    height: Optional[int] = None
    url: Optional[str] = None
    hasP3: Optional[bool] = None


class PlayParams(BaseModel):
    id: Optional[str] = None
    kind: Optional[str] = None
    versionHash: Optional[str] = None


class PlaylistAttributes(BaseModel):
    hasCollaboration: Optional[bool] = None
    curatorName: Optional[str] = None
    lastModifiedDate: Optional[str] = None
    audioTraits: List
    name: Optional[str] = None
    isChart: Optional[bool] = None
    supportsSing: Optional[bool] = None
    playlistType: Optional[str] = None
    description: Optional[Description] = None
    artwork: Optional[Artwork] = None
    playParams: PlayParams
    url: Optional[str] = None


class CuratorDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None


class Curator(BaseModel):
    href: Optional[str] = None
    data: List[CuratorDatum]


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
    albumName: Optional[str] = None
    hasTimeSyncedLyrics: Optional[bool] = None
    genreNames: List[str]
    trackNumber: Optional[int] = None
    releaseDate: Optional[str] = None
    durationInMillis: Optional[int] = None
    isVocalAttenuationAllowed: Optional[bool] = None
    isMasteredForItunes: Optional[bool] = None
    isrc: Optional[str] = None
    artwork: TrackArtwork
    composerName: Optional[str] = None
    audioLocale: Optional[str] = None
    url: Optional[str] = None
    playParams: TrackPlayParams
    discNumber: Optional[int] = None
    hasCredits: Optional[bool] = None
    isAppleDigitalMaster: Optional[bool] = None
    hasLyrics: Optional[bool] = None
    audioTraits: List[str]
    name: Optional[str] = None
    previews: List[Preview]
    artistName: Optional[str] = None


class ContentVersion(BaseModel):
    RTCI: Optional[int] = None
    MZ_INDEXER: Optional[int] = None


class TrackMeta(BaseModel):
    contentVersion: Optional[ContentVersion] = None


class TrackDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: TrackAttributes
    meta: Optional[TrackMeta] = None


class Tracks(BaseModel):
    href: Optional[str] = None
    next: Optional[str] = None
    data: List[TrackDatum]


class PlaylistRelationships(BaseModel):
    curator: Curator
    tracks: Tracks


class PlaylistDatum(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    href: Optional[str] = None
    attributes: PlaylistAttributes
    relationships: PlaylistRelationships


class PlaylistInfo(BaseModel):
    data: List[PlaylistDatum]
    songIdIndexMapping: dict[str, int] = {}
