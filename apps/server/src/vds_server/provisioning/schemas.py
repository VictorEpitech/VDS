"""Schémas d'entrée et de sortie du provisioning (distincts, Pydantic v2)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from vds_sdk.agent.provision import ProvisionStepId, StepStatus

Reason = Literal[
    "agent_offline",
    "agent_outdated",
    "module_disabled",
    "apk_unavailable",
    "server_unconfigured",
]
Status = Literal[
    "queued", "running", "waiting_user", "waiting_online", "done", "failed", "cancelled"
]


class ApkInfo(BaseModel):
    version_name: str
    version_code: int


class CapabilitiesOut(BaseModel):
    available: bool
    reason: Reason | None
    platforms: list[str]
    apk: ApkInfo | None


class ProvisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=32)
    address: str = Field(min_length=2, max_length=64)
    port: int = Field(5555, ge=1, le=65535)
    device_owner: Literal["try", "skip"] = "try"
    soft_kiosk_fallback: bool = True
    reprovision: bool = False


class ProvisionRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_step: ProvisionStepId | None = None


class StepOut(BaseModel):
    step: ProvisionStepId
    status: StepStatus
    code: str | None
    at: datetime | None


class OutcomeOut(BaseModel):
    ok: bool
    kiosk: Literal["device_owner", "soft", "none"]
    installed_version_code: int | None
    code: str | None


class ProvisionOut(BaseModel):
    id: UUID
    screen_id: UUID
    site_id: UUID
    platform: str
    address: str
    port: int
    status: Status
    steps: list[StepOut]
    outcome: OutcomeOut | None
    code: str | None
    apk_version_code: int | None
    ticket_expires_at: datetime | None
    created_at: datetime
    finished_at: datetime | None
    pairing_code_fallback: bool  # le code d'appairage manuel reste utilisable tant que non appairé
