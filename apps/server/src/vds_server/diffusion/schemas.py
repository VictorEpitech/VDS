"""Schémas d'entrée et de sortie distincts (Pydantic v2) pour médias, playlists, programmation."""

import re
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MediaKind = Literal["image", "video", "document"]
MediaStatus = Literal["processing", "ready", "failed"]
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


class MediaDetailOut(MediaOut):
    variants: list[MediaVariantOut]
    used_by: list[UUID]  # playlists qui référencent ce média


class MediaPatch(_In):
    title: str = NAME


# --- playlists ---------------------------------------------------------------
class PlaylistCreate(_In):
    name: str = NAME
    description: str | None = Field(default=None, max_length=2000)


class PlaylistPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


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
