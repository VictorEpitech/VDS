import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import pool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from vds_sdk import ContractError, Event, UndeclaredEvent
from vds_server.db.base import make_sessionmaker
from vds_server.db.models import EventDelivery, EventOutbox
from vds_server.db.models import Module as ModuleRow
from vds_server.events import CoreBus
from vds_server.registry import ModuleRegistry

REPO_MODULES = Path(__file__).resolve().parents[3] / "modules"

SERVER_PY = """
from vds_sdk import Module

class Demo(Module):
    async def setup(self, ctx):
        self.ctx = ctx
        async def boom(event):
            raise RuntimeError("x")
        ctx.bus.subscribe("screen.created", boom)
"""


def _manifest(mid: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": mid,
        "version": "0.1.0",
        "core": ">=1.2 <2.0",
        "kind": "notifier",
        "name": {"fr": "D", "en": "D"},
        "description": {"fr": "D", "en": "D"},
        "targets": ["server"],
        "entrypoints": {"server": "server.module:Demo"},
        "events": {"listens": ["screen.created"], "emits": ["notify.sent"]},
        "author": "test",
    }
    base.update(over)
    return base


def _write(root: Path, manifest: dict[str, object]) -> None:
    d = root / str(manifest["id"])
    (d / "server").mkdir(parents=True)
    (d / "module.json").write_text(json.dumps(manifest))
    (d / "server" / "module.py").write_text(SERVER_PY)


@pytest.fixture
async def maker(migrated_db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text("DELETE FROM event_outbox"))
        await c.execute(text("DELETE FROM module"))
    yield make_sessionmaker(engine)
    await engine.dispose()


async def test_builtin_modules_synced(maker: async_sessionmaker[AsyncSession]) -> None:
    reg = ModuleRegistry(maker, CoreBus(maker), REPO_MODULES, required=("theme.vtech",))
    await reg.start()
    async with maker() as s:
        rows = {r.id: r for r in (await s.execute(select(ModuleRow))).scalars()}
    assert {"theme.vtech", "theme.allia"} <= rows.keys()
    assert {"content.image", "content.video", "content.pdf", "content.url"} <= rows.keys()
    assert rows["theme.vtech"].state == "enabled" and rows["theme.vtech"].source == "builtin"
    assert rows["theme.allia"].state == "discovered"
    assert rows["theme.vtech"].config_schema is not None
    with pytest.raises(ContractError):
        await reg.disable("theme.vtech")  # requis par la configuration
    await reg.enable("theme.allia")
    await reg.disable("theme.allia")
    row = await reg.get("theme.allia")
    assert row is not None and row.state == "disabled"
    await reg.stop()


async def test_content_types_enabled_by_default(maker: async_sessionmaker[AsyncSession]) -> None:
    reg = ModuleRegistry(maker, CoreBus(maker), REPO_MODULES, required=("theme.vtech",))
    await reg.start()
    ids = ["content.image", "content.video", "content.pdf", "content.url"]
    assert all(reg.is_running(i) for i in ids)
    assert reg.content_processor("image/png") is not None
    await reg.disable("content.url")  # choix de l'admin : jamais réactivé au redémarrage
    await reg.stop()
    reg2 = ModuleRegistry(maker, CoreBus(maker), REPO_MODULES, required=("theme.vtech",))
    await reg2.start()
    assert reg2.is_running("content.image") and not reg2.is_running("content.url")
    async with maker() as s:
        row = await s.get(ModuleRow, "content.url")
    assert row is not None and row.state == "disabled"
    await reg2.stop()


async def test_undeclared_publish_refused(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write(tmp_path, _manifest("notify.demo"))
    reg = ModuleRegistry(maker, CoreBus(maker), tmp_path)
    await reg.start()
    await reg.enable("notify.demo")
    ctx = reg._running["notify.demo"].instance.ctx  # type: ignore[union-attr]
    await ctx.bus.publish("notify.sent")
    with pytest.raises(UndeclaredEvent):
        await ctx.bus.publish("screen.deleted")
    with pytest.raises(UndeclaredEvent):
        ctx.bus.subscribe("screen.updated", lambda e: None)
    await reg.stop()


async def test_quarantine_after_five_failures(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write(tmp_path, _manifest("notify.demo"))
    bus = CoreBus(maker)
    reg = ModuleRegistry(maker, bus, tmp_path)
    await reg.start()
    await reg.enable("notify.demo")
    for _ in range(4):
        await bus.publish(Event(name="screen.created"))
    assert reg.is_running("notify.demo")
    await bus.publish(Event(name="screen.created"))
    assert not reg.is_running("notify.demo")
    row = await reg.get("notify.demo")
    assert row is not None and row.state == "quarantined" and row.failure_count == 5
    async with maker() as s:
        names = (await s.execute(select(EventOutbox.name))).scalars().all()
        assert list(names) == ["module.quarantined"]
        assert (await s.execute(select(EventDelivery))).scalars().all() == []
    with pytest.raises(ContractError):
        await reg.enable("notify.nope")
    await reg.enable("notify.demo")  # sortie de quarantaine à chaud
    assert reg.is_running("notify.demo")
    await reg.stop()


async def test_critical_event_outbox_delivery(maker: async_sessionmaker[AsyncSession]) -> None:
    bus = CoreBus(maker)
    seen: list[Event] = []

    async def ok(event: Event) -> None:
        seen.append(event)

    bus.subscribe("module.quarantined", ok, subscriber="core.audit")
    await bus.publish(Event(name="module.quarantined", payload={"module_id": "x.y"}))
    assert len(seen) == 1 and seen[0].id is not None
    async with maker() as s:
        d = (await s.execute(select(EventDelivery))).scalar_one()
        assert d.status == "done" and d.subscriber == "core.audit"


async def test_incompatible_core_recorded(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    _write(tmp_path, _manifest("notify.old", core=">=2.0 <3.0", kind="player-adapter"))
    reg = ModuleRegistry(maker, CoreBus(maker), tmp_path)
    await reg.start()
    row = await reg.get("notify.old")
    assert row is not None and row.state == "incompatible"
    assert row.last_error == "module.core_incompatible"
    with pytest.raises(ContractError):
        await reg.enable("notify.old")
