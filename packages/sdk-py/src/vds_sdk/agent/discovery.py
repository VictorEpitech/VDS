"""Interface `Discoverer` (kind `discovery`, site-agent), contrat vds-core 1.5.0.

Le runtime de l'agent possède le réseau (façade `DiscoveryNet`, périmètre calculé par lui) ;
le module n'interprète que les réponses. Aucune cible réseau ne vient du serveur ni du module.
"""

import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from typing import Annotated, ClassVar, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ..errors import ContractError

DISCOVERY_CAPABILITY_RE = re.compile(r"^discovery\.[a-z][a-z0-9_-]*$")
DISCOVERY_SCOPES: frozenset[str] = frozenset({"lan"})  # 1.5 : seul `discovery.lan`
# Liste fermée des ports sondés (connect() puis fermeture, zéro octet envoyé).
DISCOVERY_PORTS: tuple[int, ...] = (5555, 8001, 8002, 8008, 8009, 3000, 3001, 9197)
MAX_DEVICES = 128
MAX_BATCH = 16
MAX_SUGGESTED_DRIVERS = 4
MAC_PATTERN = r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$"

PlatformHint = Literal["android", "google_tv", "tizen", "webos", "cast", "unknown"]
Protocol_ = Literal["adb", "samsung_ws", "cast", "dial", "webos", "airplay"]

_NO_CONTROL = re.compile(r"^[^\x00-\x1f\x7f]*$")
Text128 = Annotated[str, StringConstraints(max_length=128, pattern=_NO_CONTROL.pattern)]


class DiscoveryScopeError(Exception):
    """Cible hors périmètre ou port hors `DISCOVERY_PORTS` (levée par la façade du runtime)."""

    def __init__(self, code: str = "discovery.out_of_scope") -> None:
        super().__init__(code)
        self.code = code


def ensure_discovery_ports(ports: Sequence[int]) -> tuple[int, ...]:
    """Valide des ports contre la liste fermée ; lève DiscoveryScopeError sinon."""
    if any(p not in DISCOVERY_PORTS for p in ports):
        raise DiscoveryScopeError("discovery.port_not_allowed")
    return tuple(ports)


class DiscoveryScope(BaseModel):
    """Construit par le runtime, jamais par le serveur ni par le module."""

    model_config = ConfigDict(frozen=True)

    networks: tuple[IPv4Network, ...] = Field(min_length=1, max_length=2)
    truncated: bool
    probe_ports: bool
    deadline: datetime  # UTC, début + 30 s

    @field_validator("networks")
    @classmethod
    def _private_networks(cls, v: tuple[IPv4Network, ...]) -> tuple[IPv4Network, ...]:
        for n in v:
            if not n.is_private or n.is_loopback or n.is_link_local or n.prefixlen < 22:
                raise ValueError("discovery.network_invalid")
        return v


class DiscoveredDevice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ip: IPv4Address
    mac: str | None = Field(default=None, pattern=MAC_PATTERN)
    vendor: Text128 | None = None
    model: Text128 | None = None
    name: Text128 | None = None
    platform: PlatformHint = "unknown"
    protocols: frozenset[Protocol_] = frozenset()
    suggested_drivers: tuple[Text128, ...] = Field(default=(), max_length=MAX_SUGGESTED_DRIVERS)
    confidence: Literal["high", "medium", "low"] = "low"


class MdnsRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    service: Text128
    instance: Text128
    host: Text128
    ip: IPv4Address
    port: int = Field(ge=1, le=65535)
    txt: dict[str, str] = Field(default={}, max_length=64)

    @field_validator("txt")
    @classmethod
    def _txt_size(cls, v: dict[str, str]) -> dict[str, str]:
        if sum(len(k) + len(x) for k, x in v.items()) > 1024:
            raise ValueError("discovery.txt_too_large")
        return v


class SsdpReply(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ip: IPv4Address
    st: Text128
    server: Text128 | None = None
    location: str | None = Field(default=None, max_length=512)


class SsdpDescription(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manufacturer: Text128 | None = None
    model_name: Text128 | None = None
    friendly_name: Text128 | None = None
    device_type: Text128 | None = None


class DiscoveryNet(Protocol):
    """Fourni par le runtime ; chaque appel revalide le périmètre et les ports."""

    def neighbors(self) -> Mapping[IPv4Address, str]: ...  # IP -> MAC, lecture seule

    def mdns_browse(
        self, services: Sequence[str], window_s: float
    ) -> AsyncIterator[MdnsRecord]: ...

    def ssdp_search(self, targets: Sequence[str], window_s: float) -> AsyncIterator[SsdpReply]: ...

    async def ssdp_description(self, reply: SsdpReply) -> SsdpDescription | None: ...

    async def tcp_open(self, ip: IPv4Address, ports: Sequence[int]) -> frozenset[int]: ...

    def sweep(self, ports: Sequence[int]) -> AsyncIterator[tuple[IPv4Address, frozenset[int]]]: ...


@runtime_checkable
class Discoverer(Protocol):
    scopes: ClassVar[frozenset[str]]  # {"lan"} ; capacité `discovery.lan`

    async def discover(
        self,
        scope: DiscoveryScope,
        net: DiscoveryNet,
        report: Callable[[DiscoveredDevice], Awaitable[None]],
    ) -> None: ...


def discovery_scopes(capabilities: list[str]) -> frozenset[str]:
    return frozenset(
        c.removeprefix("discovery.") for c in capabilities if c.startswith("discovery.")
    )


def check_discoverer(instance: object, capabilities: list[str]) -> None:
    """Au chargement : `discovery.<s>` déclaré => `Discoverer` couvrant <s>."""
    declared = discovery_scopes(capabilities)
    if not declared:
        return
    if not isinstance(instance, Discoverer):
        raise ContractError("module.discoverer_missing")
    scopes = getattr(instance, "scopes", None)
    if not isinstance(scopes, frozenset) or not declared <= scopes:
        raise ContractError("module.discoverer_scope_mismatch")


__all__ = [
    "DISCOVERY_CAPABILITY_RE",
    "DISCOVERY_PORTS",
    "DISCOVERY_SCOPES",
    "MAX_BATCH",
    "MAX_DEVICES",
    "DiscoveredDevice",
    "Discoverer",
    "DiscoveryNet",
    "DiscoveryScope",
    "DiscoveryScopeError",
    "MdnsRecord",
    "PlatformHint",
    "SsdpDescription",
    "SsdpReply",
    "check_discoverer",
    "discovery_scopes",
    "ensure_discovery_ports",
]
