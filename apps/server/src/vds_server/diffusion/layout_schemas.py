"""Schémas distincts entrée/sortie des mises en page (`/api/v1/layouts`)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from vds_server.diffusion.schemas import UsageSchedule

ZoneKey = Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,32}$")]
Orientation = Literal["landscape", "portrait"]
Fit = Literal["contain", "cover", "stretch"]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
MAX_ZONES_ABSOLUTE = 32


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayoutZoneIn(_In):
    key: ZoneKey
    name: str = Field(min_length=1, max_length=80)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    z: int = Field(default=0, ge=0, le=99)
    playlist_id: UUID | None = None
    fit: Fit = "contain"
    default_item_duration_ms: int | None = Field(default=None, ge=1000, le=86_400_000)
    active_from: AwareDatetime | None = None
    active_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def _window(self) -> "LayoutZoneIn":
        if (
            self.active_from is not None
            and self.active_until is not None
            and self.active_until <= self.active_from
        ):
            raise ValueError("layout.zone_window_invalid")
        return self


class LayoutIn(_In):
    name: str = Field(min_length=1, max_length=120)
    orientation: Orientation
    background_color: Color | None = None
    background_media_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    zones: list[LayoutZoneIn] = Field(min_length=1, max_length=MAX_ZONES_ABSOLUTE)


class LayoutZoneOut(BaseModel):
    key: str
    name: str
    x: float
    y: float
    w: float
    h: float
    z: int
    playlist_id: UUID | None
    fit: Fit
    default_item_duration_ms: int | None
    active_from: datetime | None
    active_until: datetime | None


class LayoutOut(BaseModel):
    id: UUID
    name: str
    orientation: Orientation
    background_color: str | None
    background_media_id: UUID | None
    tags: list[str]
    zones: list[LayoutZoneOut]
    revision: int
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = Field(default_factory=list)


class LayoutSummary(BaseModel):
    id: UUID
    name: str
    orientation: Orientation
    tags: list[str]
    revision: int
    zone_count: int
    usage_count: int
    zones: list[LayoutZoneOut]  # géométrie seule utile à la vignette
    updated_at: datetime


class LayoutPage(BaseModel):
    items: list[LayoutSummary]
    total: int


class LayoutUsageOverride(BaseModel):
    id: UUID
    name: str
    layout_id: UUID | None
    zone_ids: list[str] | None
    ends_at: datetime
    active: bool


class LayoutUsageOut(BaseModel):
    schedules: list[UsageSchedule]
    overrides: list[LayoutUsageOverride]  # forçages d'écran sur cette mise en page
    zone_overrides: list[LayoutUsageOverride]  # forçages de zone actifs ou futurs
    screens_total: int
    screens_orientation_mismatch: int
