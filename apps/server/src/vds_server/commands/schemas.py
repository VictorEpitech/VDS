"""Schémas (entrée / sortie / événements) des commandes player et des captures (lot I)."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CommandName = Literal["reload", "restart", "identify", "screenshot", "cache.resync"]
CommandStatus = Literal["queued", "sent", "ok", "failed", "expired", "rejected", "cancelled"]
Executor = Literal["player", "agent"]
Trigger = Literal["on_demand", "periodic"]


class PlayerCommandCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: CommandName
    args: dict[str, Any] = Field(default_factory=dict)
    ttl_s: int | None = Field(default=None, ge=10, le=86400)
    request_key: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    batch_id: UUID | None = None


class PlayerCommandOut(BaseModel):
    id: UUID
    screen_id: UUID | None
    name: CommandName
    args: dict[str, Any]
    status: CommandStatus
    executor: Executor = "player"  # vds-core 1.14.0
    code: str | None
    requested_by: str | None  # nom affichable, jamais d'identifiant technique
    created_at: datetime
    expires_at: datetime
    done_at: datetime | None
    screenshot_id: UUID | None


class ScreenshotOut(BaseModel):
    id: UUID
    screen_id: UUID
    trigger: Trigger
    captured_at: datetime
    width: int
    height: int
    bytes: int
    image_url: str  # chemin relatif sous VDS_BASE_PATH


class ScreenshotCreated(BaseModel):
    screenshot_id: UUID


# --- événements du noyau (@1) -----------------------------------------------------------
class PlayerCommandRequestedV1(BaseModel):
    version: Literal[1] = 1
    command_id: UUID
    screen_id: UUID
    name: str
    actor_kind: str
    batch_id: UUID | None = None


class PlayerCommandCompletedV1(BaseModel):
    version: Literal[1] = 1
    command_id: UUID
    screen_id: UUID | None
    name: str
    status: Literal["ok", "failed", "expired", "rejected", "cancelled"]
    code: str | None = None


class ScreenScreenshotCapturedV1(BaseModel):
    version: Literal[1] = 1
    screenshot_id: UUID
    screen_id: UUID
    trigger: Trigger
    captured_at: datetime
