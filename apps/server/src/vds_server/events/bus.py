"""Bus du noyau : pub/sub en mémoire + outbox (event_outbox / event_delivery) pour les critiques.

Un événement critique est persisté avant livraison, puis livré au moins une fois (reprise par
`process_pending`). Les échecs d'un abonné sont comptés par fenêtre glissante ; au seuil, le
callback `on_quarantine` est appelé (le registre met le module en quarantaine).
"""

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.bus import EventHandler
from vds_sdk.events import CRITICAL_EVENTS_1_2

from vds_server.db.models import EventDelivery, EventOutbox

log = logging.getLogger("vds.bus")

CORE_SOURCE = "core"
CRITICAL_EVENTS: frozenset[str] = frozenset({"module.quarantined"}) | CRITICAL_EVENTS_1_2
INITIAL_DELAY_S = 5
MAX_ATTEMPTS = 5

QuarantineCallback = Callable[[str, str], Awaitable[None]]


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * 2**attempts, 300))


class _ModuleBusView:
    """Vue `EventBus` liée à un abonné (id de module) : alimente `ScopedBus`."""

    def __init__(self, bus: "CoreBus", subscriber: str) -> None:
        self._bus = bus
        self._subscriber = subscriber

    async def publish(self, event: Event) -> None:
        await self._bus.publish(event)

    def subscribe(self, name: str, handler: EventHandler) -> Callable[[], None]:
        return self._bus.subscribe(name, handler, subscriber=self._subscriber)


class CoreBus:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession] | None = None,
        *,
        critical: frozenset[str] = CRITICAL_EVENTS,
        failure_threshold: int = 5,
        failure_window_s: float = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._maker = maker
        self._critical = critical
        self.threshold = failure_threshold
        self._window = failure_window_s
        self._clock = clock
        self._subs: dict[str, dict[str, list[EventHandler]]] = defaultdict(dict)
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self.on_quarantine: QuarantineCallback | None = None

    def view(self, subscriber: str) -> EventBus:
        return _ModuleBusView(self, subscriber)

    def subscribe(
        self, name: str, handler: EventHandler, *, subscriber: str = CORE_SOURCE
    ) -> Callable[[], None]:
        handlers = self._subs[name].setdefault(subscriber, [])
        handlers.append(handler)

        def unsubscribe() -> None:
            current = self._subs[name].get(subscriber, [])
            if handler in current:
                current.remove(handler)
            if not current:
                self._subs[name].pop(subscriber, None)

        return unsubscribe

    def unsubscribe_all(self, subscriber: str) -> None:
        for by_sub in self._subs.values():
            by_sub.pop(subscriber, None)
        self._failures.pop(subscriber, None)

    async def publish(self, event: Event) -> None:
        event = event.model_copy(update={"id": uuid4(), "source": event.source or CORE_SOURCE})
        targets = {k: list(v) for k, v in self._subs.get(event.name, {}).items()}
        critical = event.name in self._critical
        if critical:
            await self._persist(event, list(targets))
        for subscriber, handlers in targets.items():
            await self._deliver(subscriber, handlers, event, critical)

    async def _persist(self, event: Event, subscribers: list[str]) -> None:
        if self._maker is None or event.id is None:
            raise RuntimeError("bus.outbox_unavailable")
        first = datetime.now(UTC) + timedelta(seconds=INITIAL_DELAY_S)
        async with self._maker.begin() as s:
            s.add(
                EventOutbox(
                    id=event.id,
                    name=event.name,
                    version=event.version,
                    source=event.source or CORE_SOURCE,
                    payload=event.payload,
                )
            )
            await s.flush()
            s.add_all(
                EventDelivery(event_id=event.id, subscriber=sub, next_attempt_at=first)
                for sub in subscribers
            )

    async def _run(self, subscriber: str, handlers: list[EventHandler], event: Event) -> str | None:
        """Exécute les handlers ; retourne un code d'erreur stable ou None."""
        error: str | None = None
        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                log.warning("handler failed", extra={"subscriber": subscriber, "event": event.name})
                error = f"bus.handler_failed:{type(exc).__name__}"
        return error

    async def _deliver(
        self, subscriber: str, handlers: list[EventHandler], event: Event, critical: bool
    ) -> None:
        error = await self._run(subscriber, handlers, event)
        if critical:
            await self._mark(event, subscriber, error)
        if error is not None:
            await self._record_failure(subscriber, error)

    async def _mark(self, event: Event, subscriber: str, error: str | None) -> None:
        if self._maker is None:
            return
        async with self._maker.begin() as s:
            row = await s.get(EventDelivery, (event.id, subscriber))
            if row is not None:
                self._apply(row, error)

    @staticmethod
    def _apply(row: EventDelivery, error: str | None) -> None:
        if error is None:
            row.status, row.next_attempt_at, row.last_error = "done", None, None
            return
        row.attempts = (row.attempts or 0) + 1
        row.last_error = error
        if row.attempts >= MAX_ATTEMPTS:
            row.status, row.next_attempt_at = "failed", None
        else:
            row.next_attempt_at = datetime.now(UTC) + _backoff(row.attempts)

    async def _record_failure(self, subscriber: str, error: str) -> None:
        if subscriber == CORE_SOURCE:
            return
        now = self._clock()
        window = self._failures[subscriber]
        window.append(now)
        while window and now - window[0] > self._window:
            window.popleft()
        if len(window) >= self.threshold and self.on_quarantine is not None:
            window.clear()
            await self.on_quarantine(subscriber, error)

    async def process_pending(self, *, limit: int = 100) -> int:
        """Rejoue les livraisons critiques échues ; retourne le nombre traité."""
        if self._maker is None:
            return 0
        now = datetime.now(UTC)
        async with self._maker() as s:
            rows = (
                await s.execute(
                    select(EventDelivery, EventOutbox)
                    .join(EventOutbox, EventOutbox.id == EventDelivery.event_id)
                    .where(EventDelivery.status == "pending", EventDelivery.next_attempt_at <= now)
                    .order_by(EventOutbox.occurred_at)
                    .limit(limit)
                )
            ).all()
            todo = [
                (
                    d.subscriber,
                    Event(
                        id=o.id, name=o.name, version=o.version, payload=o.payload, source=o.source
                    ),
                )
                for d, o in rows
            ]
        done = 0
        for subscriber, event in todo:
            handlers = list(self._subs.get(event.name, {}).get(subscriber, []))
            if not handlers:
                continue  # abonné absent (module désactivé) : reste en attente
            error = await self._run(subscriber, handlers, event)
            await self._mark(event, subscriber, error)
            if error is not None:
                await self._record_failure(subscriber, error)
            done += 1
        return done
