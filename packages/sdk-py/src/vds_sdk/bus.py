"""Interface du bus d'événements et sa vue filtrée par module (`ScopedBus`)."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from .errors import UndeclaredEvent
from .events import parse_event
from .manifest import ModuleManifest


class Event(BaseModel):
    id: UUID | None = None  # attribué par le bus à la publication (clé d'idempotence)
    name: str
    version: int = 1
    payload: dict[str, Any] = {}
    source: str | None = None  # module_id émetteur, renseigné par le ScopedBus
    origin: str = "server"  # "server" ou "agent:<site_id>" (1.2)


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    """Bus du noyau. L'implémentation (outbox, livraison) est dans apps/server."""

    async def publish(self, event: Event) -> None: ...

    def subscribe(self, name: str, handler: EventHandler) -> Callable[[], None]:
        """Abonne `handler` ; retourne la fonction de désabonnement."""
        ...


class ScopedBus:
    """Bus d'un module : refuse toute publication/abonnement non déclaré au manifeste."""

    def __init__(self, bus: EventBus, manifest: ModuleManifest) -> None:
        self._bus = bus
        self._module_id = manifest.id
        self._emits = {parse_event(e).name for e in manifest.events.emits}
        self._listens = {parse_event(e).name for e in manifest.events.listens}

    async def publish(
        self, name: str, payload: dict[str, Any] | None = None, *, version: int = 1
    ) -> None:
        if name not in self._emits:
            raise UndeclaredEvent(detail=f"emit {name} by {self._module_id}")
        await self._bus.publish(
            Event(name=name, version=version, payload=payload or {}, source=self._module_id)
        )

    def subscribe(self, name: str, handler: EventHandler) -> Callable[[], None]:
        if name not in self._listens:
            raise UndeclaredEvent(detail=f"listen {name} by {self._module_id}")
        return self._bus.subscribe(name, handler)
