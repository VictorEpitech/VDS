"""Interface `PowerDriver` (cible site-agent), contrat vds-core 1.2.0."""

from abc import abstractmethod
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, IPvAnyAddress

from ..module import Module

PowerState = Literal["on", "off", "standby", "unknown"]
Capability = Literal["power.on", "power.off", "power.state", "power.input"]

__all__ = [
    "Capability",
    "DetectHint",
    "DetectResult",
    "PowerDriver",
    "PowerResult",
    "PowerState",
    "PowerTarget",
]


class PowerTarget(BaseModel):
    screen_id: UUID
    site_timezone: str
    address: IPvAnyAddress | None = None
    mac: str | None = None
    config: dict[str, Any] = {}  # config résolue du module (secrets en SecretStr)


class PowerResult(BaseModel):
    ok: bool
    state: PowerState
    verified: bool
    code: str | None = None


class DetectHint(BaseModel):
    address: IPvAnyAddress | None = None
    mac: str | None = None
    platform: str | None = None


class DetectResult(BaseModel):
    driver: str
    confidence: float
    capabilities: frozenset[Capability]
    proposed_config: dict[str, Any] = {}  # jamais de secret
    requires_user_action: str | None = None  # clé i18n
    code: str | None = None


class PowerDriver(Module):
    capabilities: ClassVar[frozenset[Capability]]

    @abstractmethod
    async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult: ...

    @abstractmethod
    async def get_state(self, target: PowerTarget) -> PowerState:
        """`unknown` si non supporté."""
        ...

    async def detect(self, hint: DetectHint) -> DetectResult | None:
        """None = appareil absent ; borne de 10 s imposée par l'agent."""
        return None
