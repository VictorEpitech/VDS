"""Schémas d'entrée et de sortie de la découverte LAN (distincts, Pydantic v2)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

Reason = Literal["agent_missing", "agent_offline", "agent_outdated", "module_disabled"]
Status = Literal["pending", "running", "completed", "failed"]


class CapabilitiesOut(BaseModel):
    available: bool
    reason: Reason | None
    probe_ports: bool


class ScanCreate(BaseModel):
    """Corps vide : aucune cible, aucun CIDR, aucun port (le périmètre est celui de l'agent)."""

    model_config = ConfigDict(extra="forbid")


class ScanCreated(BaseModel):
    scan_id: UUID
    status: Status


class DeviceOut(BaseModel):
    id: UUID
    ip: str
    mac: str | None
    vendor: str | None
    model: str | None
    name: str | None
    platform: str
    protocols: list[str]
    suggested_drivers: list[str]
    confidence: str
    known_screen_id: UUID | None


class ScanOut(BaseModel):
    id: UUID
    status: Status
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None
    networks: list[str]
    truncated: bool
    capped: bool
    devices: list[DeviceOut]
