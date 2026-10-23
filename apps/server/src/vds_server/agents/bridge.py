"""Pont agent -> bus : republie les événements d'un agent avec origin=agent:<site_id>.

Trois contrôles (docs/architecture-lot3.md § 1.2) : nom autorisé (cœur : liste blanche ; sinon
déclaré par un module à cible site-agent), forme du payload, appartenance des écrans au site.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import CORE_EVENT_PAYLOAD_1_2, CORE_EVENTS_1_2, Event, EventBus
from vds_sdk.wire import CLOSE_BAD_MESSAGE, CLOSE_FORBIDDEN, EventData, WireError

from vds_server.db.models import Screen

# Événements du cœur qu'un agent peut émettre (agent.* est émis par le serveur seul).
AGENT_CORE_EVENTS = frozenset({"power.changed", "power.failed", "cache.synced"})
# nom d'événement -> id du module (cible site-agent) qui le déclare
ModuleEvents = Callable[[], dict[str, str]]


def _screen_ids(payload: dict[str, Any]) -> set[UUID]:
    raw: list[Any] = []
    if "screen_id" in payload:
        raw.append(payload["screen_id"])
    ids = payload.get("screen_ids")
    if isinstance(ids, list):
        raw.extend(ids)
    out: set[UUID] = set()
    for item in raw:
        try:
            out.add(UUID(str(item)))
        except ValueError as exc:
            raise WireError("invalid_payload", CLOSE_BAD_MESSAGE) from exc
    return out


class EventBridge:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        module_events: ModuleEvents,
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._module_events = module_events

    def _source(self, name: str) -> str:
        if name in AGENT_CORE_EVENTS:
            return "agent"
        if name in CORE_EVENTS_1_2:
            raise WireError("event_not_allowed", CLOSE_FORBIDDEN)
        module_id = self._module_events().get(name)
        if module_id is None:
            raise WireError("event_not_allowed", CLOSE_FORBIDDEN)
        return module_id

    async def republish(self, site_id: UUID, data: EventData) -> None:
        source = self._source(data.name)
        required = CORE_EVENT_PAYLOAD_1_2.get(data.name, ())
        if any(k not in data.payload for k in required):
            raise WireError("invalid_payload", CLOSE_BAD_MESSAGE)
        if "site_id" in data.payload and str(data.payload["site_id"]) != str(site_id):
            raise WireError("site_mismatch", CLOSE_FORBIDDEN)
        screens = _screen_ids(data.payload)
        if screens:
            async with self._maker() as s:
                owned = set(
                    (
                        await s.execute(
                            select(Screen.id).where(
                                Screen.id.in_(screens), Screen.site_id == site_id
                            )
                        )
                    ).scalars()
                )
            if owned != screens:
                raise WireError("screen_not_in_site", CLOSE_FORBIDDEN)
        await self._bus.publish(
            Event(
                name=data.name,
                version=data.version,
                payload=data.payload,
                source=source,
                origin=f"agent:{site_id}",
            )
        )
