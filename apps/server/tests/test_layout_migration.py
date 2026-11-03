"""Migration 0017_mises_en_page : CHECK (zone, XOR playlist/layout), descente et remontée."""

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import pool, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from test_db_schema import _seed, session  # noqa: F401
from vds_server.db.models import Screen


async def _world(s: AsyncSession) -> dict[str, str]:
    org, site, _ = await _seed(s)
    scr = Screen(site_id=site.id, name="e")
    s.add(scr)
    await s.flush()
    pl = (
        await s.execute(text("INSERT INTO playlist (name) VALUES ('p') RETURNING id"))
    ).scalar_one()
    lay = (
        await s.execute(
            text(
                "INSERT INTO layout (org_id, name, orientation) VALUES (:o, 'L', 'landscape') "
                "RETURNING id"
            ),
            {"o": org.id},
        )
    ).scalar_one()
    return {"org": str(org.id), "screen": str(scr.id), "pl": str(pl), "lay": str(lay)}


async def _rejected(s: AsyncSession, sql: str, **kw: object) -> None:
    with pytest.raises(IntegrityError):
        async with s.begin_nested():
            await s.execute(text(sql), kw)


ZONE = (
    "INSERT INTO layout_zone (layout_id, key, name, x, y, w, h{extra}) "
    "VALUES (:l, :k, 'n', :x, 0, :w, 10000{vals})"
)


async def test_zone_constraints(session: AsyncSession) -> None:  # noqa: F811
    w = await _world(session)
    plain = ZONE.format(extra="", vals="")
    ok = {"l": w["lay"], "k": "main", "x": 0, "w": 10000}
    await session.execute(text(plain), ok)
    await _rejected(session, plain, **{**ok, "k": "b", "x": 1})  # x + w > 1
    await _rejected(session, plain, **{**ok, "k": "c", "w": 0})
    await _rejected(session, plain, **{**ok, "k": "Bad Key"})
    await _rejected(session, plain, **ok)  # (layout, key) unique
    z100 = ZONE.format(extra=", z", vals=", 100")
    await _rejected(session, z100, **{**ok, "k": "d"})
    fit = ZONE.format(extra=", fit", vals=", 'x'")
    await _rejected(session, fit, **{**ok, "k": "e"})
    dur = ZONE.format(extra=", default_item_duration_ms", vals=", 10")
    await _rejected(session, dur, **{**ok, "k": "f"})
    await _rejected(
        session,
        "INSERT INTO layout (org_id, name, orientation, background_color) "
        "VALUES (:o, 'X', 'landscape', 'red')",
        o=w["org"],
    )
    await _rejected(
        session,
        "INSERT INTO layout (org_id, name, orientation) VALUES (:o, 'L', 'landscape')",
        o=w["org"],
    )  # (organisation, nom) unique


async def test_schedule_xor(session: AsyncSession) -> None:  # noqa: F811
    w = await _world(session)
    sql = (
        "INSERT INTO schedule (name, playlist_id, layout_id, target_type, target_id) "
        "VALUES ('s', {p}, {l}, 'screen', :t)"
    )
    t = w["screen"]
    await session.execute(text(sql.format(p=":p", l="NULL")), {"p": w["pl"], "t": t})
    await session.execute(text(sql.format(p="NULL", l=":l")), {"l": w["lay"], "t": t})
    await _rejected(session, sql.format(p=":p", l=":l"), p=w["pl"], l=w["lay"], t=t)
    await _rejected(session, sql.format(p="NULL", l="NULL"), t=t)


def _override_sql(**kw: str) -> str:
    cols = {"k": "'playlist'", "p": "NULL", "l": "NULL", "z": "NULL", **kw}
    return (  # noqa: S608
        "INSERT INTO playback_overrides (name, kind, playlist_id, layout_id, zone_ids, "
        "target_type, target_id, starts_at, ends_at) VALUES ('o', {k}, {p}, {l}, {z}, 'screen', "
        ":t, now(), now() + interval '1 hour')"
    ).format(**cols)


async def test_override_checks(session: AsyncSession) -> None:  # noqa: F811
    w = await _world(session)
    args = {"p": w["pl"], "l": w["lay"], "t": w["screen"]}
    await session.execute(text(_override_sql(p=":p")), args)  # forçage d'écran 1.7
    await session.execute(text(_override_sql(l=":l")), args)  # forçage d'écran sur mise en page
    await session.execute(text(_override_sql(p=":p", z="ARRAY['bandeau']")), args)  # de zone
    await _rejected(session, _override_sql(p=":p", l=":l"), **args)
    await _rejected(session, _override_sql(l=":l", z="ARRAY['bandeau']"), **args)  # zone + layout
    await _rejected(session, _override_sql(k="'off'", z="ARRAY['bandeau']"), **args)  # + extinction
    await _rejected(session, _override_sql(k="'off'", l=":l"), **args)
    await _rejected(session, _override_sql(p=":p", z="ARRAY[]::text[]"), **args)
    many = "ARRAY[" + ",".join(f"'z{i}'" for i in range(33)) + "]"
    await _rejected(session, _override_sql(p=":p", z=many), **args)
    await _rejected(session, _override_sql(z="ARRAY['a']"), **args)  # zone sans playlist


def test_downgrade_keeps_legacy_rows_and_upgrade_restores(
    migrated_db_url: str, alembic_cfg: Config
) -> None:
    async def run(sql: str, **kw: object) -> list[tuple[object, ...]]:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            rows = await c.execute(text(sql), kw)
            out = [tuple(r) for r in rows] if rows.returns_rows else []
        await engine.dispose()
        return out

    wipe = "TRUNCATE schedule, playback_overrides, playlist, layout, organization CASCADE"

    async def seed() -> None:
        org = (await run("INSERT INTO organization (name) VALUES ('o') RETURNING id"))[0][0]
        pl = (await run("INSERT INTO playlist (name) VALUES ('p') RETURNING id"))[0][0]
        lay = (
            await run(
                "INSERT INTO layout (org_id, name, orientation) VALUES (:o, 'L', 'portrait') "
                "RETURNING id",
                o=org,
            )
        )[0][0]
        t = "00000000-0000-0000-0000-0000000000aa"
        for p_, l_ in ((pl, None), (None, lay)):
            await run(
                "INSERT INTO schedule (name, playlist_id, layout_id, target_type, target_id) "
                "VALUES ('s', :p, :l, 'screen', :t)",
                p=p_,
                l=l_,
                t=t,
            )
        for p_, l_, z in ((pl, None, None), (None, lay, None), (pl, None, ["bandeau"])):
            await run(
                "INSERT INTO playback_overrides (name, kind, playlist_id, layout_id, zone_ids, "
                "target_type, target_id, starts_at, ends_at) VALUES ('o', 'playlist', :p, :l, :z, "
                "'screen', :t, now(), now() + interval '1 hour')",
                p=p_,
                l=l_,
                z=z,
                t=t,
            )

    asyncio.run(run(wipe))
    asyncio.run(seed())
    command.downgrade(alembic_cfg, "0016_mediatheque")
    assert asyncio.run(run("SELECT count(*) FROM schedule"))[0][0] == 1  # cible playlist gardée
    assert asyncio.run(run("SELECT count(*) FROM playback_overrides"))[0][0] == 1
    assert asyncio.run(run("SELECT to_regclass('public.layout')"))[0][0] is None
    command.upgrade(alembic_cfg, "heads")
    assert asyncio.run(run("SELECT count(*) FROM schedule WHERE layout_id IS NULL"))[0][0] == 1
    zone_null = "SELECT count(*) FROM playback_overrides WHERE zone_ids IS NULL"
    assert asyncio.run(run(zone_null))[0][0] == 1
    asyncio.run(run(wipe))
