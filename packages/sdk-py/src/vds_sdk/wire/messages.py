"""Charges utiles typées (champ `data`) des trames agent v1 / player v1."""

from __future__ import annotations

import ipaddress
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..agent.provision import TICKET_RE, ApkRef, ProvisionStepId, parse_lan_address
from .frame import Frame, WireError

# --- Listes fermées de commandes -------------------------------------------------
AGENT_COMMANDS_1_2 = (
    "power.set",
    "power.probe",
    "power.detect",
    "plan.updated",
    "config.updated",
    "cache.sync",
)
AGENT_COMMANDS = (
    *AGENT_COMMANDS_1_2,
    "screen.provision",
)  # 1.3 : un agent 1.2 répond unknown_command
PLAYER_COMMANDS = ("reload", "identify", "screenshot", "power.set", "power.probe")
CMD_STATUSES = ("ok", "failed", "expired", "rejected")
POWER_REASONS = ("manual", "schedule", "exception", "alert")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModuleRef(_M):
    id: str
    version: str


class ModuleEnabled(ModuleRef):
    config_revision: str | None = None


class HostInfo(_M):
    os: str
    arch: str


class LanInfo(_M):
    ipv4: list[str] = []


class AgentHello(_M):
    agent_version: str
    core: str
    modules: list[ModuleRef] = []
    last_server_seq: int = Field(0, ge=0)
    out_seq: int | None = Field(default=None, ge=0)  # resynchronisation seq
    host: HostInfo
    lan: LanInfo = LanInfo()


class CacheInfo(_M):
    enabled: bool = False
    key_id: str | None = None
    key: str | None = Field(default=None, repr=False)


class AgentWelcome(_M):
    agent_id: UUID
    site_id: UUID
    session_id: UUID
    heartbeat_s: int = 30
    server_time: datetime
    resume_from: int = Field(0, ge=0)
    modules_enabled: list[ModuleEnabled] = []
    plan_revision: str | None = None
    cache: CacheInfo = CacheInfo()


class ModuleStatus(_M):
    id: str
    status: str
    code: str | None = None


class PowerObserved(_M):
    observed: Literal["on", "off", "standby", "unknown"]
    driver: str | None = None
    at: datetime | None = None


class ScreenPower(_M):
    screen_id: UUID
    power: PowerObserved


class CacheStats(_M):
    enabled: bool = False
    bytes: int = 0
    items: int = 0
    missing: int = 0


class AgentHeartbeat(_M):
    uptime_s: int = 0
    clock_offset_ms: int = 0
    plan_revision: str | None = None
    modules: list[ModuleStatus] = []
    screens: list[ScreenPower] = []
    cache: CacheStats = CacheStats()


# --- Commandes (args par nom) ----------------------------------------------------
class PowerSetArgs(_M):
    state: Literal["on", "off"]
    reason: Literal["manual", "schedule", "exception", "alert"] = "manual"
    request_id: str


class EmptyArgs(_M):
    pass


def _lan_address(value: str | None) -> str | None:
    """Sonde : IP privée du LAN uniquement (ni boucle locale, ni link-local, ni publique)."""
    if value is None:
        return None
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("power.address_invalid") from exc
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise ValueError("power.address_not_lan")
    return str(ip)


class PowerDetectArgs(_M):
    address: str | None = None
    mac: str | None = None

    @field_validator("address")
    @classmethod
    def _address_is_lan(cls, value: str | None) -> str | None:
        return _lan_address(value)


class ScreenProvisionArgs(_M):
    """Commande fermée `screen.provision` (1.3). Le ticket n'est jamais journalisé."""

    provision_id: UUID
    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    address: str
    port: int = Field(5555, ge=1, le=65535)
    apk: ApkRef
    server_url: str = Field(max_length=2048, pattern=r"^https?://")
    pairing_ticket: str = Field(repr=False)
    device_owner: Literal["try", "skip"] = "try"
    soft_kiosk_fallback: bool = True
    from_step: ProvisionStepId | None = None

    @field_validator("address")
    @classmethod
    def _address_is_lan(cls, value: str) -> str:
        return parse_lan_address(value)

    @field_validator("pairing_ticket")
    @classmethod
    def _ticket(cls, value: str) -> str:
        if not TICKET_RE.match(value):
            raise ValueError("provision.ticket_invalid")
        return value


class RevisionArgs(_M):
    revision: str


class ConfigUpdatedArgs(_M):
    module_id: str
    config_revision: str


class IdentifyArgs(_M):
    seconds: int = Field(10, ge=1, le=30)


class PlayerPowerSetArgs(_M):
    state: Literal["on", "off"]
    request_id: str


AGENT_COMMAND_ARGS: dict[str, type[_M]] = {
    "power.set": PowerSetArgs,
    "power.probe": EmptyArgs,
    "power.detect": PowerDetectArgs,
    "plan.updated": RevisionArgs,
    "config.updated": ConfigUpdatedArgs,
    "cache.sync": RevisionArgs,
    "screen.provision": ScreenProvisionArgs,
}
PLAYER_COMMAND_ARGS: dict[str, type[_M]] = {
    "reload": EmptyArgs,
    "identify": IdentifyArgs,
    "screenshot": EmptyArgs,
    "power.set": PlayerPowerSetArgs,
    "power.probe": EmptyArgs,
}


class Cmd(_M):
    cmd_id: UUID
    name: str
    screen_id: UUID | None = None
    args: dict[str, Any] = {}
    expires_at: datetime
    correlation_id: str | None = None

    def validate_for(self, channel: Literal["agent", "player"]) -> _M:
        """Valide `name` (liste fermée) et `args`. Lève ValueError('unknown_command')."""
        table = AGENT_COMMAND_ARGS if channel == "agent" else PLAYER_COMMAND_ARGS
        model = table.get(self.name)
        if model is None:
            raise ValueError("unknown_command")
        return model.model_validate(self.args)


class CmdResult(_M):
    cmd_id: UUID
    status: Literal["ok", "failed", "expired", "rejected"]
    code: str | None = None
    data: dict[str, Any] = {}


class EventData(_M):
    name: str
    version: int = 1
    payload: dict[str, Any] = {}
    occurred_at: datetime


# --- Player ----------------------------------------------------------------------
class PlayerAuth(_M):
    token: str = Field(min_length=1)


class PlayerWelcome(_M):
    device_id: UUID
    screen_id: UUID
    heartbeat_s: int = 60
    server_time: datetime
    revision: str | None = None


class ScheduleChanged(_M):
    revision: str


# type de trame -> modèle de data
AGENT_FRAME_DATA: dict[str, type[_M]] = {
    "hello": AgentHello,
    "welcome": AgentWelcome,
    "heartbeat": AgentHeartbeat,
    "cmd": Cmd,
    "cmd.result": CmdResult,
    "event": EventData,
    "ack": EmptyArgs,
}
PLAYER_FRAME_DATA: dict[str, type[_M]] = {
    "auth": PlayerAuth,
    "welcome": PlayerWelcome,
    "schedule.changed": ScheduleChanged,
    "cmd": Cmd,
    "cmd.result": CmdResult,
    "event": EventData,
    "ack": EmptyArgs,
    "heartbeat": BaseModel,  # type: ignore[dict-item]  # même payload que POST player/heartbeat
}

# Sens autorisés
AGENT_TO_SERVER = frozenset({"hello", "heartbeat", "cmd.result", "event", "ack"})
SERVER_TO_AGENT = frozenset({"welcome", "cmd", "ack"})
PLAYER_TO_SERVER = frozenset({"auth", "heartbeat", "cmd.result", "event", "ack"})
SERVER_TO_PLAYER = frozenset({"welcome", "schedule.changed", "cmd", "ack"})
PLAYER_EVENTS = frozenset({"power.changed", "power.failed"})


def parse_data(
    frame: Frame, channel: Literal["agent", "player"], direction: Literal["to_server", "to_peer"]
) -> Any:
    """Valide type, sens et `data` d'une trame ; lève WireError(bad_message)."""
    allowed = {
        ("agent", "to_server"): AGENT_TO_SERVER,
        ("agent", "to_peer"): SERVER_TO_AGENT,
        ("player", "to_server"): PLAYER_TO_SERVER,
        ("player", "to_peer"): SERVER_TO_PLAYER,
    }[(channel, direction)]
    if frame.type not in allowed:
        raise WireError("unexpected_type")
    table = AGENT_FRAME_DATA if channel == "agent" else PLAYER_FRAME_DATA
    model = table[frame.type]
    if model is BaseModel:
        return frame.data
    try:
        parsed = model.model_validate(frame.data)
    except ValueError as exc:
        raise WireError("invalid_data") from exc
    if channel == "player" and frame.type == "event" and direction == "to_server":
        if parsed.name not in PLAYER_EVENTS:  # type: ignore[attr-defined]
            raise WireError("event_not_allowed")
    return parsed
