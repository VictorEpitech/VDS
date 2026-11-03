from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from vds_sdk.wire import CmdResult


class DeviceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(min_length=1, max_length=40)
    model: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=40)
    app_version_code: int | None = Field(default=None, ge=1, le=2_100_000_000)
    user_agent: str | None = Field(default=None, max_length=400)
    screen_w: int | None = Field(default=None, ge=0, le=20000)
    screen_h: int | None = Field(default=None, ge=0, le=20000)


class PairingStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device: DeviceInfo


class PairingStarted(BaseModel):
    pairing_id: UUID
    code: str
    poll_secret: str
    expires_at: datetime
    poll_interval_s: int


class TicketPair(BaseModel):
    """Appairage par ticket de provisioning (additif) ; le ticket n'est jamais journalisé."""

    model_config = ConfigDict(extra="forbid")
    pairing_ticket: str = Field(
        min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", repr=False
    )
    device: DeviceInfo


class PairingClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=6, max_length=12)
    screen_id: UUID


class PairingClaimed(BaseModel):
    screen_id: UUID


class PairingDone(BaseModel):
    device_token: str
    screen_id: UUID
    server_time: datetime


class PairingPending(BaseModel):
    status: str = "pending"


class CacheState(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bytes: int | None = Field(default=None, ge=0)
    quota: int | None = Field(default=None, ge=0)
    missing: list[str] = Field(default_factory=list, max_length=500)


class KioskReport(BaseModel):
    """État appliqué rapporté par le player (jamais affiché au public)."""

    model_config = ConfigDict(extra="ignore")
    mode: Literal["locked", "unlocked", "maintenance"]
    enforcement: Literal["lock_task", "soft", "web"]
    confirmed: bool | None = None  # False : réponse native en échec (état non confirmé)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    revision: str | None = Field(default=None, max_length=100)
    playing: dict[str, Any] | None = None
    cache: CacheState | None = None
    app_version: str | None = Field(default=None, max_length=40)
    app_version_code: int | None = Field(default=None, ge=1, le=2_100_000_000)
    uptime_s: int | None = Field(default=None, ge=0)
    stale: bool = False
    kiosk: KioskReport | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    # vds-core 1.11.0 (additif) : capacités, démarrage, résultats de commandes
    capabilities: list[Annotated[str, Field(max_length=40)]] | None = Field(
        default=None, max_length=20
    )
    boot_id: UUID | None = None
    command_results: list[CmdResult] = Field(default_factory=list, max_length=20)


class HeartbeatAck(BaseModel):
    revision: str | None
    server_time: datetime
    poll_interval_s: int
    commands: list[dict[str, Any]]
    results_acked: list[UUID] = Field(default_factory=list)  # 1.11.0


# --- programmation résolue (SCHEDULE_FORMAT = 1) ------------------------------
class ScreenRef(BaseModel):
    id: UUID
    name: str
    orientation: Literal["landscape", "portrait"]


class LayoutZone(BaseModel):
    id: str
    x: float
    y: float
    w: float
    h: float
    playlist: str


class Layout(BaseModel):
    zones: list[LayoutZone]


class SlotSource(BaseModel):
    """Origine du créneau. vds-core 1.7.0 (additif) : `kind` et `override_id` ; `schedule_id`
    est null pour un forçage. Un client < 1.7.0 ignore les clés qu'il ne connaît pas."""

    schedule_id: UUID | None
    kind: Literal["schedule", "override"] = "schedule"
    override_id: UUID | None = None


class OffSlot(BaseModel):
    start: str
    end: str
    kind: Literal["off"] = "off"
    mode: Literal["black"] = "black"
    override_id: UUID | None = None  # 1.7.0 : extinction forcée (absent = hors plage)


class PlaySlot(BaseModel):
    start: str
    end: str
    kind: Literal["play"] = "play"
    layout: Layout
    source: SlotSource


class PlayerItem(BaseModel):
    id: str
    content_type: str
    duration_ms: int | None
    props: dict[str, Any]
    assets: list[str]
    requires_network: bool
    until: datetime | None = None  # vds-core 1.8.0 : fin de diffusion (média expirant)


class PlayerPlaylist(BaseModel):
    items: list[PlayerItem]


class AssetRef(BaseModel):
    sha256: str | None
    bytes: int
    mime: str


class ContentTypeEntry(BaseModel):
    version: str
    element: str
    script: str


class KioskDoc(BaseModel):
    mode: Literal["locked", "unlocked", "maintenance"]
    until: str | None = None  # échéance de la maintenance (UTC), sinon null


class ScreenshotDoc(BaseModel):
    interval_min: int


class ScheduleDocument(BaseModel):
    format: Literal[1] = 1
    revision: str
    server_time: datetime
    screen: ScreenRef
    timezone: str
    valid_from: str
    valid_until: str
    slots: list[Annotated[OffSlot | PlaySlot, Field(discriminator="kind")]]
    playlists: dict[str, PlayerPlaylist]
    assets: dict[str, AssetRef]
    content_types: dict[str, ContentTypeEntry]
    kiosk: KioskDoc | None = None  # absent (vieux serveur) = locked
    screenshot: ScreenshotDoc | None = None  # 1.11.0 : capture périodique ; absent = désactivée
