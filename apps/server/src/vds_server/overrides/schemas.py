"""Schémas distincts entrée/sortie du forçage (`/api/v1/overrides`) et payloads du bus (v1)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from vds_server.diffusion.schemas import TargetType

OverrideKind = Literal["playlist", "off"]
OverrideStatus = Literal["scheduled", "active", "ended"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverrideContentIn(_In):
    """« Forcer un contenu » : devient une playlist éphémère d'un élément."""

    content_type: str = Field(min_length=1, max_length=120)
    props: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = Field(default=None, gt=0)


class OverrideCreate(_In):
    target_type: TargetType
    target_id: UUID
    kind: OverrideKind = "playlist"
    playlist_id: UUID | None = None  # exactement un de playlist_id / content si kind=playlist
    content: OverrideContentIn | None = None
    starts_at: AwareDatetime | None = None  # Avancé ; défaut maintenant
    ends_at: AwareDatetime | None = None  # exactement un de ends_at / duration_minutes / until
    duration_minutes: int | None = Field(default=None, gt=0)
    until: Literal["end_of_day"] | None = None
    priority: int = Field(default=0, ge=-1000, le=1000)
    respect_scheduled_off: bool | None = None  # None = valeur de config résolue
    name: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    request_id: UUID | None = None  # idempotence (double clic, fan-out admin)


class OverridePatch(_In):
    ends_at: AwareDatetime | None = None
    note: str | None = Field(default=None, max_length=500)


class OverrideOut(BaseModel):
    id: UUID
    name: str
    kind: OverrideKind
    playlist_id: UUID | None
    ephemeral: bool
    target_type: TargetType
    target_id: UUID
    target_name: str
    starts_at: datetime
    ends_at: datetime
    ended_at: datetime | None
    end_reason: Literal["stopped", "expired"] | None
    status: OverrideStatus
    priority: int
    respect_scheduled_off: bool
    note: str | None
    created_by: UUID | None
    created_by_module: str | None
    created_at: datetime


class OverridePreviewOut(BaseModel):
    screens_total: int
    screens_offline: int  # l'arrêt anticipé ne les atteindra pas
    screens_in_scheduled_off: int  # seront rallumés (ou ignorés si respect_scheduled_off)
    ends_at: datetime  # fin calculée
    superseded_override_ids: list[UUID]  # forçages actifs qui seront masqués


# --- bus (version 1) -----------------------------------------------------------------
class OverrideCreatedV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    kind: OverrideKind
    playlist_id: UUID | None
    starts_at: datetime
    ends_at: datetime
    created_by: UUID | None
    created_by_module: str | None


class OverrideUpdatedV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    ends_at: datetime


class OverrideStoppedV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    ended_at: datetime
    ended_by: UUID | None


class OverrideExpiredV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    ended_at: datetime
