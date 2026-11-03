"""Migration 0019_commandes_player : contraintes, index partiels, cascades, descente/remontée."""
# ruff: noqa: F811

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_db_schema import _seed, session  # noqa: F401
from vds_server.db.models import Screen

INS = (
    "INSERT INTO player_command (screen_id, name, status, actor_kind, actor_id, request_key, "
    "expires_at) VALUES (:s, :n, :st, :k, :a, :r, now() + interval '10 minutes') RETURNING id"
)


def _args(s: str, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "s": s,
        "n": "reload",
        "st": "queued",
        "k": "user",
        "a": "u1",
        "r": None,
    }
    return {**base, **over}


async def _rejected(sess: AsyncSession, sql: str, **kw: object) -> None:
    with pytest.raises(IntegrityError):
        async with sess.begin_nested():
            await sess.execute(text(sql), kw)


async def _screen(s: AsyncSession) -> str:
    _, site, _ = await _seed(s)
    scr = Screen(site_id=site.id, name="e")
    s.add(scr)
    await s.flush()
    return str(scr.id)


async def test_command_constraints(session: AsyncSession) -> None:
    scr = await _screen(session)
    for name in ("reload", "restart", "identify", "screenshot", "cache.resync"):
        await session.execute(text(INS), _args(scr, n=name))
    for st in ("sent", "ok", "failed", "expired", "rejected", "cancelled"):
        await session.execute(text(INS), _args(scr, st=st))
    await _rejected(session, INS, **_args(scr, n="power.set"))
    await _rejected(session, INS, **_args(scr, st="done"))
    await _rejected(session, INS, **_args(scr, k="robot"))
    # idempotence : (actor_id, request_key) unique si request_key non nul
    await session.execute(text(INS), _args(scr, r="k1"))
    await _rejected(session, INS, **_args(scr, r="k1"))
    await session.execute(text(INS), _args(scr, r="k1", a="u2"))
    await session.execute(text(INS), _args(scr, r=None))  # NULL : pas de contrainte


async def test_screenshot_constraints_and_cascade(session: AsyncSession) -> None:
    scr = await _screen(session)
    cmd = (await session.execute(text(INS), _args(scr, n="screenshot"))).scalar_one()
    ins = (
        "INSERT INTO screenshot (screen_id, command_id, trigger, captured_at, width, height, "
        "bytes, sha256, storage_key) VALUES (:s, :c, :t, now(), :w, 720, 1000, 'x', :k)"
    )
    ok: dict[str, Any] = {"s": scr, "c": str(cmd), "t": "on_demand", "w": 1280, "k": "k/1"}
    await session.execute(text(ins), ok)
    await _rejected(session, ins, **ok)  # storage_key unique
    await _rejected(session, ins, **{**ok, "k": "k/2", "t": "manual"})
    await _rejected(session, ins, **{**ok, "k": "k/3", "w": 0})
    await session.execute(text("DELETE FROM player_command WHERE id = :c"), {"c": str(cmd)})
    n = await session.execute(text("SELECT count(*) FROM screenshot WHERE command_id IS NULL"))
    assert n.scalar_one() == 1  # image conservée, lien rompu
    await session.execute(text("DELETE FROM screen WHERE id = :s"), {"s": scr})
    assert (await session.execute(text("SELECT count(*) FROM screenshot"))).scalar_one() == 0


async def test_screen_deletion_keeps_command_history(session: AsyncSession) -> None:
    scr = await _screen(session)
    await session.execute(text(INS), _args(scr))
    await session.execute(text("DELETE FROM screen WHERE id = :s"), {"s": scr})
    row = await session.execute(text("SELECT screen_id FROM player_command"))
    assert row.scalar_one() is None


def test_0019_downgrade_removes_then_restores(migrated_db_url: str, alembic_cfg: Config) -> None:
    async def run(stmt: str) -> list[Any]:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            res = await c.execute(text(stmt))
            rows = list(res.all()) if res.returns_rows else []
        await engine.dispose()
        return rows

    def reg() -> tuple[Any, Any]:
        r = asyncio.run(run("SELECT to_regclass('player_command'), to_regclass('screenshot')"))
        return r[0][0], r[0][1]

    assert all(reg())
    command.downgrade(alembic_cfg, "0018_perimetre")
    assert reg() == (None, None)
    # la descente n'altère aucune table antérieure
    assert asyncio.run(run("SELECT to_regclass('user_scope')"))[0][0] is not None
    command.upgrade(alembic_cfg, "heads")
    assert all(reg())
