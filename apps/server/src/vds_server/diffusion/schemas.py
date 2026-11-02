"""Schémas d'entrée et de sortie distincts (Pydantic v2) pour médias, playlists, programmation."""

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MediaKind = Literal["image", "video", "document"]
MediaStatus = Literal["processing", "ready", "failed"]
MediaState = Literal[
    "active", "expiring", "expired", "all"
]  # expiring = core.media.expiring_soon_days
MediaSort = Literal[
    "-created_at", "created_at", "title", "-title", "-bytes", "expires_at", "-updated_at"
]
PlaylistSort = Literal["name", "-name", "-created_at", "created_at", "expires_at", "-updated_at"]
TargetType = Literal["organization", "site", "group", "screen"]
NAME = Field(min_length=1, max_length=120)
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$|^24:00$")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- médias ------------------------------------------------------------------
class MediaVariantOut(_Out):
    name: str
    mime: str
    bytes: int
    sha256: str | None
    width: int | None
    height: int | None


class MediaOut(_Out):
    id: UUID
    kind: MediaKind
    title: str
    original_filename: str | None
    mime: str
    bytes: int
    sha256: str | None
    width: int | None
    height: int | None
    duration_ms: int | None
    page_count: int | None
    status: MediaStatus
    error: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    expired: bool  # expiration constatée ou échue (le balayeur peut avoir jusqu'à 60 s de retard)
    revision: int
    tags: list[str]
    thumb: str | None  # clé relative "media/<id>/thumb" si la variante existe, sinon None
    usage_count: int  # nombre de playlists qui le référencent


class MediaDetailOut(MediaOut):
    variants: list[MediaVariantOut]
    used_by: list[UUID]  # playlists qui référencent ce média


class MediaPage(_Out):
    items: list[MediaOut]
    total: int


class MediaPatch(_In):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=100)  # bornes exactes : service
    expires_at: datetime | None = None  # présent (même null) = modifier ; null = jamais

    @model_validator(mode="after")
    def _not_empty(self) -> "MediaPatch":
        if not self.model_fields_set:
            raise ValueError("media.patch_empty")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("media.title_null")
        if "tags" in self.model_fields_set and self.tags is None:
            raise ValueError("media.tags_null")
        return self


class UsagePlaylist(_Out):
    id: UUID
    name: str
    expires_at: datetime | None
    expired: bool


class UsageSchedule(_Out):
    id: UUID
    name: str
    playlist_id: UUID
    target_type: str
    target_id: UUID
    target_name: str
    active: bool


class UsageOverride(_Out):
    id: UUID
    name: str
    playlist_id: UUID
    ends_at: datetime
    active: bool


class MediaUsageOut(_Out):
    playlists: list[UsagePlaylist]
    schedules: list[UsageSchedule]
    overrides: list[UsageOverride]
    screens_now: int  # borne supérieure, indicative


class PlaylistUsageOut(_Out):
    schedules: list[UsageSchedule]
    overrides: list[UsageOverride]


BulkAction = Literal["set_expiry", "add_tags", "remove_tags", "delete"]


class MediaBulkIn(_In):
    ids: list[UUID] = Field(min_length=1, max_length=200)
    action: BulkAction
    expires_at: datetime | None = None  # set_expiry : null = jamais
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _coherent(self) -> "MediaBulkIn":
        if self.action == "set_expiry" and "expires_at" not in self.model_fields_set:
            raise ValueError("media.bulk_expiry_missing")
        if self.action in ("add_tags", "remove_tags") and not self.tags:
            raise ValueError("media.bulk_tags_missing")
        return self


class MediaBulkOut(_Out):
    done: list[UUID]
    failed: list[dict[str, str]]  # {id, code}


# --- playlists ---------------------------------------------------------------
class PlaylistCreate(_In):
    name: str = NAME
    description: str | None = Field(default=None, max_length=2000)


class PlaylistPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=100)  # bornes exactes : service
    expires_at: datetime | None = None  # présent (même null) = modifier ; null = jamais


class ItemCreate(_In):
    content_type: str = Field(min_length=1, max_length=120)
    props: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = Field(default=None, gt=0)
    enabled: bool = True
    position: int | None = Field(default=None, ge=0)  # absent = en fin de liste


class ItemPatch(_In):
    props: dict[str, Any] | None = None
    duration_ms: int | None = Field(default=None, gt=0)  # présent (même null) = modifier
    enabled: bool | None = None


class ItemOrder(_In):
    item_ids: list[UUID]


class ItemOut(_Out):
    id: UUID
    position: int
    content_type: str
    props: dict[str, Any]
    duration_ms: int | None
    enabled: bool
    media_ids: list[UUID]


class PlaylistOut(_Out):
    id: UUID
    name: str
    description: str | None
    item_count: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    expired: bool
    tags: list[str]


class PlaylistPage(_Out):
    items: list[PlaylistOut]
    total: int


class PlaylistDetailOut(PlaylistOut):
    items: list[ItemOut]


# --- programmation -----------------------------------------------------------
class RuleIn(_In):
    weekdays: int = Field(ge=1, le=127)  # masque : lundi = 1 ... dimanche = 64
    start: str = Field(pattern=TIME_RE.pattern)  # HH:MM
    end: str = Field(pattern=TIME_RE.pattern)  # HH:MM, 24:00 admis

    @model_validator(mode="after")
    def _ordered(self) -> "RuleIn":
        if to_minutes(self.start) >= to_minutes(self.end):
            raise ValueError("schedule.rule_range_invalid")
        return self


class RuleOut(BaseModel):
    weekdays: int
    start: str
    end: str


class ScheduleCreate(_In):
    name: str = NAME
    playlist_id: UUID
    target_type: TargetType
    target_id: UUID
    priority: int = Field(default=0, ge=-1000, le=1000)
    starts_on: date | None = None
    ends_on: date | None = None
    enabled: bool = True
    rules: list[RuleIn] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _period(self) -> "ScheduleCreate":
        if self.starts_on and self.ends_on and self.starts_on > self.ends_on:
            raise ValueError("schedule.period_invalid")
        return self


class SchedulePatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    playlist_id: UUID | None = None
    target_type: TargetType | None = None
    target_id: UUID | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)
    starts_on: date | None = None  # présent (même null) = modifier
    ends_on: date | None = None
    enabled: bool | None = None
    rules: list[RuleIn] | None = Field(default=None, max_length=50)  # remplace l'ensemble


class ScheduleOut(BaseModel):
    id: UUID
    name: str
    playlist_id: UUID
    target_type: TargetType
    target_id: UUID
    priority: int
    starts_on: date | None
    ends_on: date | None
    enabled: bool
    rules: list[RuleOut]
    created_at: datetime
    updated_at: datetime


def to_minutes(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
