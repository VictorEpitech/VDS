"""Façade `ScreensApi` (cible server), contrat vds-core 1.11.0 (lot I)."""

from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

__all__ = ["ScreenCommandRequest", "ScreensApi"]


class ScreenCommandRequest(BaseModel):
    """Commande player demandée par un module. `restart` et `cache.resync` : admin seul en V1."""

    screen_id: UUID
    name: Literal["reload", "identify", "screenshot"]
    args: dict[str, Any] = Field(default_factory=dict)
    ttl_s: int = Field(default=600, ge=30, le=86400)


class ScreensApi(Protocol):
    async def send_command(self, req: ScreenCommandRequest) -> UUID:
        """Met la commande en file (le noyau la livre). Exige la permission `screens.command`."""
        ...
