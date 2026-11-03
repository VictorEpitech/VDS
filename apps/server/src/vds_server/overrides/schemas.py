"""Schémas distincts entrée/sortie du forçage (`/api/v1/overrides`) et payloads du bus (v1)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from vds_server.diffusion.schemas import TargetType

OverrideKind = Literal["playlist", "off"]
OverrideStatus = Literal["scheduled", "active", "ended"]
ZoneKey = Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,32}$")]


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
    playlist_id: UUID | None = None  # exactement un de playlist_id / content / layout_id
    content: OverrideContentIn | None = None
    layout_id: UUID | None = None  # vds-core 1.9.0 : forçage d'écran sur une mise en page
    # vds-core 1.9.0 : forçage de zone (None = forçage d'écran). Exige playlist_id, jamais de
    # layout_id ni d'extinction (validé par le service : codes stables).
    zone_ids: list[ZoneKey] | None = Field(default=None, min_length=1, max_length=32)
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
    layout_id: UUID | None = None
    zone_ids: list[str] | None = None
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
    # création seulement : avertissements non bloquants (`override_zone_missing`,
    # `layout_orientation_mismatch`) et nombre d'écrans concernés
    warnings: list[str] = Field(default_factory=list)
    screens_missing: int = 0
    screens_orientation_mismatch: int = 0


class ZoneCoverage(BaseModel):
    zone_id: str
    screens_total: int
    screens_missing: int


class ZoneCandidate(BaseModel):
    key: str
    name: str
    layouts: int  # nombre de mises en page (implicite comprise) qui ont cette zone


class OverridePreviewOut(BaseModel):
    screens_total: int
    screens_offline: int  # l'arrêt anticipé ne les atteindra pas
    screens_in_scheduled_off: int  # seront rallumés (ou ignorés si respect_scheduled_off)
    ends_at: datetime  # fin calculée
    superseded_override_ids: list[UUID]  # forçages actifs qui seront masqués
    zone_coverage: list[ZoneCoverage] = Field(default_factory=list)
    zone_candidates: list[ZoneCandidate] = Field(default_factory=list)
    screens_orientation_mismatch: int = 0


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
    zone_ids: list[str] | None = None  # 1.9.0 : None = forçage d'écran


class OverrideUpdatedV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    ends_at: datetime
    zone_ids: list[str] | None = None


class OverrideStoppedV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    ended_at: datetime
    ended_by: UUID | None
    zone_ids: list[str] | None = None


class OverrideExpiredV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID
    target_type: str
    target_id: UUID
    ended_at: datetime
    zone_ids: list[str] | None = None
