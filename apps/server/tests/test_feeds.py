"""Service `feeds` : ensure, quota, rafraîchissement, backoff, événements, plafond, purge."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from vds_sdk import ContractError, Event, Module
from vds_sdk.server.content import ContentType
from vds_sdk.server.feeds import FeedRequest
from vds_sdk.server.http import SafeHttp, SafeHttpError, SafeResponse
from vds_server.db.models_feed import ContentFeed
from vds_server.feeds import service as svc
from vds_server.feeds.service import FeedService, backoff_s, clamp_refresh


class Mod(ContentType):
    def __init__(self) -> None:
        self.outcomes: list[Any] = []
        self.calls = 0
        self.hosts_in_flight = 0
        self.max_in_flight = 0

    async def fetch_feed(self, req: FeedRequest, http: SafeHttp) -> dict[str, Any]:
        self.calls += 1
        out = self.outcomes.pop(0) if self.outcomes else {"ok": self.calls}
        if isinstance(out, Exception):
            raise out
        return out  # type: ignore[no-any-return,unused-ignore]


class NoHttp:
    async def get(self, url: str, **kw: Any) -> SafeResponse:
        raise SafeHttpError("net.timeout")


class Bus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)

    def subscribe(self, *a: Any, **k: Any) -> Any:
        raise NotImplementedError


@pytest.fixture
async def maker(migrated_db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE content_feed"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make(maker: async_sessionmaker[AsyncSession]) -> tuple[FeedService, Mod, Bus]:
    mod, bus = Mod(), Bus()

    def instance(module_id: str) -> Module | None:
        return mod if module_id == "content.test" else None

    def http(module_id: str) -> SafeHttp:
        return NoHttp()

    return FeedService(maker, bus, instance, http), mod, bus


REQ = FeedRequest(params={"lat": 48.8566, "lon": 2.3522}, refresh_s=600)


async def row(maker: async_sessionmaker[AsyncSession], feed_id: str) -> ContentFeed:
    async with maker() as s:
        r = await s.get(ContentFeed, feed_id)
        assert r is not None
        return r


async def force_due(maker: async_sessionmaker[AsyncSession], feed_id: str) -> None:
    async with maker.begin() as s:
        await s.execute(
            text("UPDATE content_feed SET next_at = now() - interval '1 second' WHERE id = :i"),
            {"i": feed_id},
        )


def test_helpers() -> None:
    assert clamp_refresh(10) == 300 and clamp_refresh(10**9) == 86400 and clamp_refresh(900) == 900
    assert [backoff_s(n, 600) for n in (1, 2, 3, 4, 5, 6)] == [60, 120, 240, 480, 600, 600]


async def test_ensure_idempotent_and_clamped(maker: async_sessionmaker[AsyncSession]) -> None:
    service, _, _ = make(maker)
    a = await service.ensure("content.test", FeedRequest(params={"b": 1, "a": 2}, refresh_s=1))
    b = await service.ensure("content.test", FeedRequest(params={"a": 2, "b": 1}, refresh_s=1))
    other = await service.ensure("content.other", FeedRequest(params={"a": 2, "b": 1}, refresh_s=1))
    assert a == b != other and len(a) == 32
    r = await row(maker, a)
    assert r.refresh_s == 300 and r.status == "never" and r.data is None
    await service.ensure("content.test", FeedRequest(params={"a": 2, "b": 1}, refresh_s=900))
    assert (await row(maker, a)).refresh_s == 900


async def test_quota_201st_refused(maker: async_sessionmaker[AsyncSession]) -> None:
    service, _, _ = make(maker)
    for i in range(200):
        await service.ensure("content.test", FeedRequest(params={"i": i}, refresh_s=600))
    with pytest.raises(ContractError) as exc:
        await service.ensure("content.test", FeedRequest(params={"i": 200}, refresh_s=600))
    assert exc.value.code == "feeds.quota_exceeded"
    # un flux existant reste utilisable, un autre module a son propre quota
    await service.ensure("content.test", FeedRequest(params={"i": 3}, refresh_s=600))
    await service.ensure("content.other", FeedRequest(params={"i": 200}, refresh_s=600))


async def test_due_refresh_stores_data(maker: async_sessionmaker[AsyncSession]) -> None:
    service, mod, bus = make(maker)
    fid = await service.ensure("content.test", REQ)
    mod.outcomes = [{"temp": 12, "_next_after_s": 7200}]
    assert await service.run_once() == 1
    r = await row(maker, fid)
    assert r.status == "ok" and r.data == {"temp": 12} and r.fetched_at is not None
    assert r.next_at - r.fetched_at >= timedelta(seconds=7200 - 1)  # Expires allonge l'intervalle
    assert await service.run_once() == 0  # pas encore dû
    assert not bus.events


async def test_failure_keeps_data_and_single_failed_event(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    service, mod, bus = make(maker)
    fid = await service.ensure("content.test", REQ)
    mod.outcomes = [{"v": 1}]
    await service.run_once()
    for i in range(1, 6):
        await force_due(maker, fid)
        mod.outcomes = [SafeHttpError("net.timeout")]
        await service.run_once()
        r = await row(maker, fid)
        assert r.data == {"v": 1} and r.status == "failing" and r.error_code == "net.timeout"
        assert r.fail_count == i
    names = [e.name for e in bus.events]
    assert names == ["feed.failed"]  # une seule fois, au 3e échec
    p = bus.events[0].payload
    assert p["module_id"] == "content.test" and p["feed_id"] == fid and p["version"] == 1
    assert p["error_code"] == "net.timeout" and p["failing_since"]
    # backoff : prochain essai avant refresh_s, plafonné
    r = await row(maker, fid)
    assert (r.next_at - datetime.now(UTC)).total_seconds() <= r.refresh_s


async def test_recovery_emits_once_and_resets(maker: async_sessionmaker[AsyncSession]) -> None:
    service, mod, bus = make(maker)
    fid = await service.ensure("content.test", REQ)
    for _ in range(3):
        await force_due(maker, fid)
        mod.outcomes = [ContractError("rss.bad_feed")]
        await service.run_once()
    await force_due(maker, fid)
    mod.outcomes = [{"v": 2}]
    await service.run_once()
    r = await row(maker, fid)
    assert r.status == "ok" and r.fail_count == 0 and r.error_code is None
    assert r.failing_since is None and r.data == {"v": 2}
    assert [e.name for e in bus.events] == ["feed.failed", "feed.recovered"]
    assert bus.events[1].payload["fetched_at"]
    # un échec court (< 3) suivi d'un succès n'émet rien
    bus.events.clear()
    await force_due(maker, fid)
    mod.outcomes = [SafeHttpError("net.timeout")]
    await service.run_once()
    await force_due(maker, fid)
    mod.outcomes = [{"v": 3}]
    await service.run_once()
    assert not bus.events


async def test_too_large_and_invalid_are_failures(maker: async_sessionmaker[AsyncSession]) -> None:
    service, mod, _ = make(maker)
    fid = await service.ensure("content.test", REQ)
    mod.outcomes = [{"x": "a" * (64 * 1024)}]
    await service.run_once()
    assert (await row(maker, fid)).error_code == "feeds.too_large"
    await force_due(maker, fid)
    mod.outcomes = [{"x": "a" * (64 * 1024 - 20)}]  # juste sous le plafond
    await service.run_once()
    assert (await row(maker, fid)).status == "ok"
    await force_due(maker, fid)
    mod.outcomes = [["not", "an", "object"]]
    await service.run_once()
    assert (await row(maker, fid)).error_code == "feeds.invalid_data"


async def test_crash_and_missing_module_do_not_stop_loop(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    service, mod, _ = make(maker)
    a = await service.ensure("content.test", REQ)
    gone = await service.ensure("content.gone", REQ)
    mod.outcomes = [RuntimeError("boom")]
    await service.run_once()
    assert (await row(maker, a)).error_code == "feeds.fetch_failed"
    assert (await row(maker, gone)).error_code == "feeds.module_unavailable"


async def test_parallelism_capped_at_4(maker: async_sessionmaker[AsyncSession]) -> None:
    service, mod, _ = make(maker)
    for i in range(10):
        await service.ensure("content.test", FeedRequest(params={"i": i}, refresh_s=600))
    running = 0
    peak = 0

    async def slow(req: FeedRequest, http: SafeHttp) -> dict[str, Any]:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return {"ok": True}

    mod.fetch_feed = slow  # type: ignore[method-assign]
    assert await service.run_once() == 10
    assert peak == svc.PARALLEL_FETCHES == 4


async def test_purge_after_7_days(maker: async_sessionmaker[AsyncSession]) -> None:
    service, _, _ = make(maker)
    old = await service.ensure("content.test", FeedRequest(params={"k": "old"}, refresh_s=600))
    new = await service.ensure("content.test", FeedRequest(params={"k": "new"}, refresh_s=600))
    async with maker.begin() as s:
        await s.execute(
            text("UPDATE content_feed SET last_used_at = now() - interval '7 days 1 hour' "
                 "WHERE id = :i"), {"i": old},
        )  # fmt: skip
    assert await service.purge(datetime.now(UTC)) == 1
    async with maker() as s:
        assert await s.get(ContentFeed, old) is None
        assert await s.get(ContentFeed, new) is not None


async def test_migration_down_up(migrated_db_url: str, alembic_cfg: Config) -> None:
    from alembic import command

    def reg() -> bool:
        async def go() -> bool:
            engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
            async with engine.begin() as c:
                res = await c.execute(text("SELECT to_regclass('content_feed') IS NOT NULL"))
                found = bool(res.scalar_one())
            await engine.dispose()
            return found

        return asyncio.run(go())

    assert await asyncio.to_thread(reg)
    await asyncio.to_thread(command.downgrade, alembic_cfg, "0019_commandes_player")
    assert not await asyncio.to_thread(reg)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "heads")
    assert await asyncio.to_thread(reg)
