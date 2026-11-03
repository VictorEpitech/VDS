"""Service `feeds` (vds-core 1.12.0) : cache de données JSON bornées pour les content-types.

Le noyau ne connaît ni la météo ni le RSS : il stocke, planifie le rafraîchissement, applique les
limites et publie `feed.failed` / `feed.recovered`. La logique (quelle URL, quel parsing) reste
dans `ContentType.fetch_feed` du module.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import ContractError, Event, EventBus, Module
from vds_sdk.server.content import ContentType
from vds_sdk.server.feeds import FeedRequest, compute_feed_id
from vds_sdk.server.http import SafeHttp, SafeHttpError, SafeResponse

from vds_server.db.models_feed import ContentFeed
from vds_server.feeds.schemas import FeedFailedV1, FeedRecoveredV1

log = logging.getLogger("vds.feeds")

MIN_REFRESH_S = 300
MAX_REFRESH_S = 86400
MAX_FEEDS_PER_MODULE = 200
MAX_DATA_BYTES = 64 * 1024
PARALLEL_FETCHES = 4
TICK_S = 30.0
FAILED_AFTER = 3
BACKOFF_BASE_S = 60
FETCH_TIMEOUT_S = 30.0
LEASE_S = 120  # un flux pris en charge n'est pas repris par un autre worker pendant ce délai
PURGE_AFTER = timedelta(days=7)
TOUCH_EVERY = timedelta(hours=1)
BATCH = 50
# Clé réservée du résultat de `fetch_feed` : délai minimal avant le prochain rafraîchissement
# (ex. en-tête `Expires`). Retirée avant stockage ; ne peut qu'allonger l'intervalle.
NEXT_AFTER_KEY = "_next_after_s"

InstanceLookup = Callable[[str], Module | None]
HttpFactory = Callable[[str], SafeHttp]


class FeedError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def clamp_refresh(refresh_s: int) -> int:
    return max(MIN_REFRESH_S, min(int(refresh_s), MAX_REFRESH_S))


def validate_data(data: object) -> dict[str, Any]:
    """JSON sérialisable, objet, <= 64 Kio ; sinon `feeds.too_large` / `feeds.invalid_data`."""
    if not isinstance(data, dict):
        raise FeedError("feeds.invalid_data")
    try:
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise FeedError("feeds.invalid_data") from exc
    if len(raw) > MAX_DATA_BYTES:
        raise FeedError("feeds.too_large")
    return data


def backoff_s(fail_count: int, refresh_s: int) -> int:
    """Exponentiel (60 s, 120 s, 240 s…), plafonné à `refresh_s`."""
    return min(BACKOFF_BASE_S << min(max(fail_count - 1, 0), 20), refresh_s)


class _HostLimited:
    """`SafeHttp` d'un module : une requête à la fois par hôte (verrous partagés du service)."""

    def __init__(self, inner: SafeHttp, locks: dict[str, asyncio.Lock]) -> None:
        self._inner = inner
        self._locks = locks

    async def get(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_s: float = 10.0,
        headers: Mapping[str, str] | None = None,
        accept: tuple[str, ...] = (),
    ) -> SafeResponse:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        async with self._locks.setdefault(host, asyncio.Lock()):
            return await self._inner.get(
                url, max_bytes=max_bytes, timeout_s=timeout_s, headers=headers, accept=accept
            )


class FeedService:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: EventBus,
        instance_of: InstanceLookup,
        http_for: HttpFactory,
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._instance_of = instance_of
        self._http_for = http_for
        self._gate = asyncio.Semaphore(PARALLEL_FETCHES)
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_purge: datetime | None = None

    # --- API module (ctx.core.feeds) -------------------------------------------------

    async def ensure(self, module_id: str, req: FeedRequest) -> str:
        feed_id = compute_feed_id(module_id, req.canonical())
        refresh = clamp_refresh(req.refresh_s)
        now = datetime.now(UTC)
        async with self._maker.begin() as s:
            existing = await s.get(ContentFeed, feed_id)
            if existing is None:
                count = await s.scalar(
                    select(func.count())
                    .select_from(ContentFeed)
                    .where(ContentFeed.module_id == module_id)
                )
                if (count or 0) >= MAX_FEEDS_PER_MODULE:
                    raise ContractError("feeds.quota_exceeded")
            stmt = insert(ContentFeed).values(
                id=feed_id,
                module_id=module_id,
                request=req.canonical(),
                refresh_s=refresh,
                status="never",
                next_at=now,
                last_used_at=now,
            )
            # `last_used_at` n'est réécrit qu'une fois par heure (ensure est appelé à chaque
            # résolution de programmation) ; un changement de fréquence est pris tout de suite.
            stmt = stmt.on_conflict_do_update(
                index_elements=[ContentFeed.id],
                set_={"last_used_at": now, "refresh_s": refresh},
                where=(ContentFeed.last_used_at < now - TOUCH_EVERY)
                | (ContentFeed.refresh_s != refresh),
            )
            await s.execute(stmt)
        return feed_id

    # --- lecture (route player) ------------------------------------------------------

    async def get(self, feed_id: str) -> ContentFeed | None:
        async with self._maker() as s:
            return await s.get(ContentFeed, feed_id)

    # --- rafraîchissement ------------------------------------------------------------

    async def run_once(self, now: datetime | None = None) -> int:
        """Un tick : purge éventuelle, puis rafraîchit les flux dus. Renvoie le nombre traité."""
        now = now or datetime.now(UTC)
        await self.purge(now)
        due = await self._claim(now)
        await asyncio.gather(*(self._refresh(feed_id) for feed_id in due))
        return len(due)

    async def _claim(self, now: datetime) -> list[str]:
        """Prend à bail les flux dus (multi-workers : SKIP LOCKED + report de `next_at`)."""
        async with self._maker.begin() as s:
            rows = (
                await s.execute(
                    select(ContentFeed)
                    .where(ContentFeed.next_at <= now)
                    .order_by(ContentFeed.next_at)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            ids = []
            for row in rows:
                row.next_at = now + timedelta(seconds=LEASE_S)
                ids.append(row.id)
        return ids

    async def purge(self, now: datetime) -> int:
        if self._last_purge is not None and now - self._last_purge < timedelta(hours=1):
            return 0
        self._last_purge = now
        async with self._maker.begin() as s:
            res = await s.execute(
                delete(ContentFeed).where(ContentFeed.last_used_at < now - PURGE_AFTER)
            )
        return int(getattr(res, "rowcount", 0) or 0)

    async def _refresh(self, feed_id: str) -> None:
        async with self._gate:
            async with self._maker() as s:
                row = await s.get(ContentFeed, feed_id)
                if row is None:
                    return
                module_id, params, refresh = row.module_id, row.request, row.refresh_s
            try:
                data, next_after = await self._fetch(module_id, params, refresh)
            except FeedError as exc:
                await self._record_failure(feed_id, exc.code)
            except Exception:  # un module défaillant ne doit jamais arrêter la boucle
                log.exception("feed fetch crashed (module %s)", module_id)
                await self._record_failure(feed_id, "feeds.fetch_failed")
            else:
                await self._record_success(feed_id, data, next_after)

    async def _fetch(
        self, module_id: str, request: dict[str, Any], refresh_s: int
    ) -> tuple[dict[str, Any], int]:
        instance = self._instance_of(module_id)
        if not isinstance(instance, ContentType):
            raise FeedError("feeds.module_unavailable")
        req = FeedRequest(params=dict(request.get("params") or {}), refresh_s=refresh_s)
        http = _HostLimited(self._http_for(module_id), self._host_locks)
        try:
            data = await asyncio.wait_for(instance.fetch_feed(req, http), FETCH_TIMEOUT_S)
        except SafeHttpError as exc:
            raise FeedError(exc.code) from exc
        except ContractError as exc:
            raise FeedError(exc.code) from exc
        except NotImplementedError as exc:
            raise FeedError("feeds.not_supported") from exc
        except TimeoutError as exc:
            raise FeedError("net.timeout") from exc
        if not isinstance(data, dict):
            raise FeedError("feeds.invalid_data")
        data = dict(data)
        raw_next = data.pop(NEXT_AFTER_KEY, 0)
        next_after = raw_next if isinstance(raw_next, int) and raw_next > 0 else 0
        return validate_data(data), min(next_after, MAX_REFRESH_S)

    async def _record_success(self, feed_id: str, data: dict[str, Any], next_after: int) -> None:
        now = datetime.now(UTC)
        event: Event | None = None
        async with self._maker.begin() as s:
            row = await s.get(ContentFeed, feed_id, with_for_update=True)
            if row is None:
                return
            if row.fail_count >= FAILED_AFTER:  # `feed.failed` avait été émis
                payload = FeedRecoveredV1(module_id=row.module_id, feed_id=row.id, fetched_at=now)
                event = Event(
                    name="feed.recovered", payload=payload.model_dump(mode="json"), source="core"
                )
            row.data, row.fetched_at, row.status = data, now, "ok"
            row.error_code, row.fail_count, row.failing_since = None, 0, None
            row.next_at = now + timedelta(seconds=max(row.refresh_s, next_after))
        if event is not None:
            await self._bus.publish(event)

    async def _record_failure(self, feed_id: str, code: str) -> None:
        now = datetime.now(UTC)
        event: Event | None = None
        async with self._maker.begin() as s:
            row = await s.get(ContentFeed, feed_id, with_for_update=True)
            if row is None:
                return
            row.fail_count += 1
            row.status, row.error_code = "failing", code
            if row.failing_since is None:
                row.failing_since = now
            row.next_at = now + timedelta(seconds=backoff_s(row.fail_count, row.refresh_s))
            if row.fail_count == FAILED_AFTER:  # une seule fois par épisode
                payload = FeedFailedV1(
                    module_id=row.module_id,
                    feed_id=row.id,
                    error_code=code,
                    failing_since=row.failing_since,
                )
                event = Event(
                    name="feed.failed", payload=payload.model_dump(mode="json"), source="core"
                )
        if event is not None:
            await self._bus.publish(event)

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                await self.run_once()
            except Exception:
                log.exception("feed tick failed")
