# ruff: noqa: F811
"""Cohérence multi-workers (2 hubs sur la même base) et mineurs sécurité M1, M6, M8-M11."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from test_agents import _queue_clean, setup, sql  # noqa: F401
from test_auth import SAME, admin, anon, app, base, clean, p  # noqa: F401
from test_power_api import RULE, attach
from vds_sdk.wire import PowerDetectArgs, WireError
from vds_sdk.wire.messages import EventData
from vds_server.agents.bridge import EventBridge
from vds_server.agents.hub import AgentHub
from vds_server.agents.plan import PlanPusher
from vds_server.agents.queue import MODULES_KIND, CommandQueue, notify, notify_raw
from vds_server.config.resolver import ChainNode, StoredOverride, resolve
from vds_server.middleware.upload_limit import UploadLimitMiddleware
from vds_server.settings import Settings


class FakeSession:
    def __init__(self) -> None:
        self.refreshed = 0

    def wake(self) -> None: ...

    async def replaced(self) -> None: ...

    async def refresh(self) -> None:
        self.refreshed += 1


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    e = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    yield e
    await e.dispose()


async def settle(cond: Any) -> None:
    for _ in range(100):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timeout")


async def test_modules_changed_reaches_every_hub(engine: AsyncEngine) -> None:
    h1, h2 = AgentHub(), AgentHub()
    s1, s2 = FakeSession(), FakeSession()
    await h1.register(uuid4(), s1)
    await h2.register(uuid4(), s2)
    h1.start(engine)
    h2.start(engine)
    try:
        await asyncio.sleep(0.5)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker.begin() as s:
            await notify(s, MODULES_KIND, UUID(int=0))
        await settle(lambda: s1.refreshed == 1 and s2.refreshed == 1)
    finally:
        await h1.stop()
        await h2.stop()


async def test_device_connection_latest_wins_across_hubs(engine: AsyncEngine) -> None:
    h1, h2 = AgentHub(), AgentHub()
    closed: list[str] = []

    def closer(name: str) -> Any:
        async def c() -> None:
            closed.append(name)

        return c

    h1.start(engine)
    h2.start(engine)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    dev = uuid4()
    try:
        await asyncio.sleep(0.5)
        n1 = h1.register_device(dev, closer("a"))
        async with maker.begin() as s:
            await notify_raw(s, "device", f"{dev}:{n1}")
        await asyncio.sleep(0.3)
        assert closed == []  # seule connexion : rien à fermer
        n2 = h2.register_device(dev, closer("b"))
        async with maker.begin() as s:
            await notify_raw(s, "device", f"{dev}:{n2}")
        await settle(lambda: closed == ["a"])  # l'ancienne (autre worker) est fermée
        await asyncio.sleep(0.3)
        assert closed == ["a"]  # la nouvelle reste ouverte
    finally:
        await h1.stop()
        await h2.stop()


async def test_plan_push_dedup_is_shared_between_workers(
    admin: AsyncClient, p: str, setup: dict[str, Any], migrated_db_url: str, app: Any
) -> None:
    plan = (
        await admin.post(
            f"{p}/api/v1/power/plans", json={"name": "P", "rules": [RULE]}, headers=SAME
        )
    ).json()["id"]
    await attach(admin, p, "site", setup["site"], "power.plan_id", plan)
    n = (await sql(migrated_db_url, "SELECT count(*) FROM remote_command"))[0][0]
    assert n >= 1
    rt = app.state.runtime
    other = PlanPusher(rt.maker, rt.schemas, CommandQueue(rt.maker))  # autre worker, sans mémoire
    await other._push({"scope_level": "site", "scope_id": setup["site"]})
    assert (await sql(migrated_db_url, "SELECT count(*) FROM remote_command"))[0][0] == n


async def test_bridge_nested_targets(app: Any, setup: dict[str, Any]) -> None:
    rt = app.state.runtime
    bridge = EventBridge(rt.maker, rt.bus, lambda: {"x.evt": "x"})
    site = UUID(setup["site"])

    def ev(payload: dict[str, Any]) -> EventData:
        return EventData(
            name="x.evt", version=1, payload=payload, occurred_at=datetime.now(UTC)
        )

    with pytest.raises(WireError):
        await bridge.republish(site, ev({"group_id": str(uuid4())}))
    with pytest.raises(WireError):
        await bridge.republish(site, ev({"a": {"device_id": "x"}}))
    with pytest.raises(WireError):  # écran imbriqué d'un autre site
        await bridge.republish(site, ev({"a": [{"screen_id": str(uuid4())}]}))


def test_power_detect_lan_only() -> None:
    assert PowerDetectArgs(address="192.168.1.20").address == "192.168.1.20"
    assert PowerDetectArgs().address is None
    for bad in ("8.8.8.8", "127.0.0.1", "169.254.169.254", "::1", "nope", "0.0.0.0"):  # noqa: S104
        with pytest.raises(ValidationError):
            PowerDetectArgs(address=bad)


def test_secret_key_min_length() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="court")  # noqa: S106
    assert Settings(secret_key="x" * 32).secret_key  # noqa: S106


def test_env_bound_key_ignores_stored_override() -> None:
    site = ChainNode("site", uuid4(), "S")
    schema = {
        "properties": {"k": {"type": "string", "default": "d", "x-vds-env": "VDS_K"}},
    }
    stale = [StoredOverride("site", site.id, "k", "ancienne")]
    res = resolve(schema, [site], stale, {}, "site")
    assert res.values["k"].value == "d"


async def test_upload_limit_cached() -> None:
    calls = 0

    async def fetch() -> int:
        nonlocal calls
        calls += 1
        return 10

    async def noop(*_: Any) -> None: ...

    mw = UploadLimitMiddleware(noop, Settings(), fetch)
    assert await mw.limit() == 10
    assert await mw.limit() == 10
    assert calls == 1
