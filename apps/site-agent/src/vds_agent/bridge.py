"""Pont bus module -> trames `event` fiables (outbox)."""

from typing import Any, Protocol

from vds_sdk import Event


class EventSink(Protocol):
    async def emit(
        self, name: str, payload: dict[str, Any], *, version: int, source: str
    ) -> None: ...


class AgentBus:
    """EventBus minimal côté agent : publie vers le serveur, ne reçoit rien."""

    def __init__(self, sink: EventSink) -> None:
        self._sink = sink

    async def publish(self, event: Event) -> None:
        await self._sink.emit(
            event.name, event.payload, version=event.version, source=event.source or "agent"
        )

    def subscribe(self, name: str, handler: Any) -> Any:
        return lambda: None
