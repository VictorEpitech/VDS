"""Forçage : API /api/v1/overrides, diffusion, alimentation, événements, audit, purge.

Exécuté aux deux VDS_BASE_PATH (fixture `p`)."""
# ruff: noqa: F811

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, audit_actions, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, _diffusion_clean, make_media, mk_playlist  # noqa: F401
from test_fleet_playback import playback, schedule, screen_of, site_of
from vds_sdk import Event
from vds_server.db.models import Playlist
from vds_server.db.models_override import PlaybackOverride
from vds_server.db.models_power import PowerException
from vds_server.overrides import schemas as sc
from vds_server.overrides.service import OverrideService
from vds_server.overrides.sweeper import OverrideSweeper
from vds_server.player.schedule import ScheduleBuilder
from vds_server.power.plan import PowerPlanBuilder


@pytest.fixture(autouse=True)
async def _override_clean(migrated_db_url: str) -> AsyncIterator[None]:
    async def truncate() -> None:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            await c.execute(
                text("TRUNCATE playback_overrides, playlist, power_exception, audit_log CASCADE")
            )
        await engine.dispose()

    await truncate()
    yield
    await truncate()


async def create(c: AsyncClient, p: str, **body: Any) -> Any:
    return await c.post(f"{p}/api/v1/overrides", json=body, headers=SAME)


async def world(admin: AsyncClient, p: str) -> dict[str, str]:
    site = await site_of(admin, p, "a")
    grp = (
        await admin.post(f"{p}/api/v1/sites/{site}/groups", json={"name": "G"}, headers=SAME)
    ).json()["id"]
    return {
        "site": site,
        "group": grp,
        "in_group": await screen_of(admin, p, site, "Groupe", grp),
        "alone": await screen_of(admin, p, site, "Seul"),
        "pl_sched": await mk_playlist(admin, p, "Prog"),
        "pl_force": await mk_playlist(admin, p, "Forcee"),
    }


async def test_scope_and_priority_over_schedule(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    sch = await schedule(admin, p, w["pl_sched"], "screen", w["in_group"], "Prog")
    r = await create(
        admin, p, target_type="group", target_id=w["group"], playlist_id=w["pl_force"],
        duration_minutes=60,
    )  # fmt: skip
    assert r.status_code == 201, r.text
    ov = r.json()
    assert ov["status"] == "active" and ov["target_name"] == "G" and ov["ephemeral"] is False
    rows = await playback(admin, p)
    cur = rows[w["in_group"]]
    # le forçage de groupe gagne sur la programmation d'écran (plus spécifique)
    assert cur["source"] == "override" and cur["override_id"] == ov["id"]
    assert cur["playlist_name"] == "Forcee" and cur["schedule_id"] is None and cur["until"]
    assert rows[w["alone"]]["source"] == "none"  # hors de la portée du groupe
    # dernier créé gagne entre forçages
    r2 = await create(
        admin, p, target_type="screen", target_id=w["in_group"], playlist_id=w["pl_sched"],
        duration_minutes=10,
    )  # fmt: skip
    assert (await playback(admin, p))[w["in_group"]]["override_id"] == r2.json()["id"]
    # site : atteint aussi l'écran isolé
    await create(
        admin, p, target_type="site", target_id=w["site"], playlist_id=w["pl_force"],
        duration_minutes=5,
    )  # fmt: skip
    assert (await playback(admin, p))[w["alone"]]["source"] == "override"
    assert sch  # programmation conservée


async def test_stop_returns_to_schedule_and_is_idempotent(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    sch = await schedule(admin, p, w["pl_sched"], "screen", w["alone"], "Prog")
    ov = (
        await create(
            admin,
            p,
            target_type="screen",
            target_id=w["alone"],
            playlist_id=w["pl_force"],
            duration_minutes=60,
        )  # fmt: skip
    ).json()
    url = f"{p}/api/v1/overrides/{ov['id']}"
    r = await admin.post(f"{url}/stop", headers=SAME)
    assert r.status_code == 200 and r.json()["status"] == "ended"
    assert r.json()["end_reason"] == "stopped"
    again = await admin.post(f"{url}/stop", headers=SAME)
    assert again.status_code == 200 and again.json()["ended_at"] == r.json()["ended_at"]
    cur = (await playback(admin, p))[w["alone"]]
    assert cur["source"] == "schedule" and cur["schedule_id"] == sch
    r = await admin.patch(url, json={"note": "x"}, headers=SAME)
    assert r.status_code == 409 and r.json()["code"] == "override.already_ended"


async def test_patch_extend_and_active_listing(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    ov = (
        await create(
            admin,
            p,
            target_type="screen",
            target_id=w["alone"],
            playlist_id=w["pl_force"],
            duration_minutes=30,
        )  # fmt: skip
    ).json()
    new_end = datetime.fromisoformat(ov["ends_at"]) + timedelta(hours=1)
    r = await admin.patch(
        f"{p}/api/v1/overrides/{ov['id']}",
        json={"ends_at": new_end.isoformat(), "note": "n"},
        headers=SAME,
    )
    assert r.status_code == 200 and r.json()["note"] == "n"
    assert datetime.fromisoformat(r.json()["ends_at"]) == new_end
    r = await admin.patch(
        f"{p}/api/v1/overrides/{ov['id']}",
        json={"ends_at": "2000-01-01T00:00:00Z"},
        headers=SAME,
    )
    assert r.status_code == 422 and r.json()["code"] == "override.end_in_past"
    lst = (await admin.get(f"{p}/api/v1/overrides?active=true&site_id={w['site']}")).json()
    assert [o["id"] for o in lst] == [ov["id"]]
    await admin.post(f"{p}/api/v1/overrides/{ov['id']}/stop", headers=SAME)
    assert (await admin.get(f"{p}/api/v1/overrides?active=true")).json() == []
    assert len((await admin.get(f"{p}/api/v1/overrides?active=false")).json()) == 1
    one = await admin.get(f"{p}/api/v1/overrides/{ov['id']}")
    assert one.status_code == 200
    miss = await admin.get(f"{p}/api/v1/overrides/{uuid4()}")
    assert miss.status_code == 404 and miss.json()["code"] == "override.not_found"


async def test_validations(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    base_ = {"target_type": "screen", "target_id": w["alone"], "playlist_id": w["pl_force"]}

    async def code(**extra: Any) -> tuple[int, str]:
        r = await create(admin, p, **{**base_, **extra})
        return r.status_code, r.json().get("code", "")

    assert await code() == (422, "override.end_required")
    assert await code(duration_minutes=5, until="end_of_day") == (422, "override.end_required")
    assert await code(ends_at="2000-01-01T00:00:00Z") == (422, "override.end_in_past")
    assert await code(duration_minutes=60 * 24 * 8) == (422, "override.duration_too_long")
    assert await code(duration_minutes=5, playlist_id=None) == (422, "override.content_ambiguous")
    both = {"content": {"content_type": "content.image", "props": {}}}
    assert await code(duration_minutes=5, **both) == (422, "override.content_ambiguous")
    assert await code(duration_minutes=5, target_id=str(uuid4())) == (
        422,
        "override.target_not_found",
    )
    assert await code(duration_minutes=5, playlist_id=str(uuid4())) == (
        422,
        "override.playlist_not_found",
    )
    assert (await code(duration_minutes=5, ends_at=None, priority=5000))[0] == 422
    naive = await create(admin, p, **base_, ends_at="2999-01-01T00:00:00")
    assert naive.status_code == 422
    assert (await create(admin, p, **base_, duration_minutes=5, extra=1)).status_code == 422


async def test_rights_origin_and_auth(
    admin: AsyncClient, editor: AsyncClient, anon: AsyncClient, p: str
) -> None:
    w = await world(admin, p)
    body = {"target_type": "screen", "target_id": w["alone"], "playlist_id": w["pl_force"]}
    ok = await create(editor, p, **body, duration_minutes=5)
    assert ok.status_code == 201  # un éditeur force une playlist
    off = await create(
        editor, p, **{**body, "kind": "off", "playlist_id": None}, duration_minutes=5
    )
    assert off.status_code == 403 and off.json()["code"] == "override.forbidden_kind"
    assert (
        await create(admin, p, **{**body, "kind": "off", "playlist_id": None}, duration_minutes=5)
    ).status_code == 201
    async with new_client(admin._transport.app) as other:  # type: ignore[attr-defined]
        r = await other.post(f"{p}/api/v1/overrides", json=body, headers=SAME)
        assert r.status_code == 401
    r = await admin.post(
        f"{p}/api/v1/overrides",
        json={**body, "duration_minutes": 5},
        headers={"Origin": "https://evil.test"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "errors.origin_mismatch"
    r = await admin.post(
        f"{p}/api/v1/overrides/{ok.json()['id']}/stop", headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert r.status_code == 403


async def test_editor_cannot_force_organization(
    app: FastAPI, admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    w = await world(admin, p)
    async with app.state.runtime.maker() as s:
        org = (await s.execute(text("SELECT id FROM organization"))).scalar_one()
    body = {"target_type": "organization", "target_id": str(org), "playlist_id": w["pl_force"]}
    r = await create(editor, p, **body, duration_minutes=5)
    assert r.status_code == 403 and r.json()["code"] == "override.forbidden_kind"
    assert (await create(admin, p, **body, duration_minutes=5)).status_code == 201


async def test_idempotent_request_id(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    rid = str(uuid4())
    body = {
        "target_type": "screen", "target_id": w["alone"], "playlist_id": w["pl_force"],
        "duration_minutes": 5, "request_id": rid,
    }  # fmt: skip
    a = await create(admin, p, **body)
    b = await create(admin, p, **body)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert len((await admin.get(f"{p}/api/v1/overrides")).json()) == 1


async def test_content_override_uses_ephemeral_playlist(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    w = await world(admin, p)
    media = await make_media(migrated_db_url)
    r = await create(
        admin, p, target_type="screen", target_id=w["alone"], duration_minutes=15,
        content={"content_type": "content.image", "props": {"src": str(media)},
                 "duration_ms": 4000},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    ov = r.json()
    assert ov["ephemeral"] is True and ov["playlist_id"]
    names = [x["id"] for x in (await admin.get(f"{p}/api/v1/playlists")).json()["items"]]
    assert ov["playlist_id"] not in names and w["pl_force"] in names  # masquée des listes
    rt = app.state.runtime

    async def tz(_: UUID) -> str:
        return "Europe/Paris"

    async with rt.maker() as s:
        doc = await ScheduleBuilder(
            s, rt.registry.content_item_schema, rt.registry.content_entry, tz
        ).build(UUID(w["alone"]))
    play = [x for x in doc["slots"] if x["kind"] == "play"]
    assert len(play) == 1
    assert play[0]["source"]["kind"] == "override" and play[0]["source"]["override_id"] == ov["id"]
    assert play[0]["source"]["schedule_id"] is None
    pl_items = doc["playlists"][f"pl_{ov['playlist_id']}"]["items"]
    assert len(pl_items) == 1 and pl_items[0]["duration_ms"] == 4000
    # trame lisible par un client 1.6 : mêmes clés de créneau, champs 1.7 ajoutés seulement
    assert {"start", "end", "kind", "layout", "source"} <= set(play[0])
    assert (datetime.fromisoformat(play[0]["end"]) - datetime.now(UTC)) <= timedelta(minutes=15)


async def test_off_override_wire_and_power(admin: AsyncClient, app: FastAPI, p: str) -> None:
    w = await world(admin, p)
    rt = app.state.runtime
    ov = (
        await create(admin, p, target_type="screen", target_id=w["alone"], kind="off",
                     duration_minutes=30)
    ).json()  # fmt: skip
    cur = (await playback(admin, p))[w["alone"]]
    assert cur["source"] == "override" and cur["override_id"] == ov["id"]
    assert cur["playlist_id"] is None

    async def tz(_: UUID) -> str:
        return "Europe/Paris"

    async with rt.maker() as s:
        doc = await ScheduleBuilder(
            s, rt.registry.content_item_schema, rt.registry.content_entry, tz
        ).build(UUID(w["alone"]))
        power = await PowerPlanBuilder(s, rt.schemas).for_screen(UUID(w["alone"]))
    off = [x for x in doc["slots"] if x.get("override_id") == ov["id"]]
    assert off and off[0]["kind"] == "off"
    now = datetime.now(UTC)
    assert any(x.state == "off" and x.start <= now < x.end for x in power.slots)


async def test_lifecycle_playback_plan_extend_stop_expire(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    """Bout en bout : création -> flotte + plan player + plan agent -> prolongation -> arrêt ->
    nouveau forçage échu -> balayeur -> retour à la programmation."""
    w = await world(admin, p)
    rt = app.state.runtime
    sch = await schedule(admin, p, w["pl_sched"], "screen", w["alone"], "Prog")

    async def tz(_: UUID) -> str:
        return "Europe/Paris"

    def mine(doc: dict[str, Any], oid: str) -> list[dict[str, Any]]:
        def oid_of(x: dict[str, Any]) -> Any:
            return x.get("override_id") or (x.get("source") or {}).get("override_id")

        return [x for x in doc["slots"] if oid_of(x) == oid]

    async def views() -> tuple[dict[str, Any], Any]:
        async with rt.maker() as s:
            doc = await ScheduleBuilder(
                s, rt.registry.content_item_schema, rt.registry.content_entry, tz
            ).build(UUID(w["alone"]))
            power = await PowerPlanBuilder(s, rt.schemas).for_screen(UUID(w["alone"]))
        return doc, power

    ov = (
        await create(admin, p, target_type="screen", target_id=w["alone"], kind="off",
                     duration_minutes=15)
    ).json()  # fmt: skip
    cur = (await playback(admin, p))[w["alone"]]
    assert cur["source"] == "override" and cur["override_id"] == ov["id"]
    doc, power = await views()
    assert mine(doc, ov["id"])
    now = datetime.now(UTC)
    assert any(x.state == "off" and x.start <= now < x.end for x in power.slots)

    new_end = datetime.fromisoformat(ov["ends_at"]) + timedelta(minutes=60)
    r = await admin.patch(
        f"{p}/api/v1/overrides/{ov['id']}", json={"ends_at": new_end.isoformat()}, headers=SAME
    )
    assert r.status_code == 200
    cur = (await playback(admin, p))[w["alone"]]
    assert datetime.fromisoformat(str(cur["until"])) == new_end
    doc, power = await views()
    assert max(datetime.fromisoformat(x["end"]) for x in mine(doc, ov["id"])) == new_end
    assert any(x.state == "off" and x.end >= new_end for x in power.slots)

    assert (
        await admin.post(f"{p}/api/v1/overrides/{ov['id']}/stop", headers=SAME)
    ).status_code == 200
    cur = (await playback(admin, p))[w["alone"]]
    assert cur["source"] == "schedule" and cur["schedule_id"] == sch
    doc, power = await views()
    # l'arrêt tronque la fin du créneau (le player le sait sans poussée), il ne reste pas actif
    assert all(datetime.fromisoformat(x["end"]) <= datetime.now(UTC) for x in mine(doc, ov["id"]))
    assert not any(x.state == "off" and x.start <= datetime.now(UTC) < x.end for x in power.slots)

    # expiration : un forçage de contenu échu est clos par le balayeur, la programmation reprend
    ov2 = (
        await create(admin, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=15)
    ).json()  # fmt: skip
    assert (await playback(admin, p))[w["alone"]]["override_id"] == ov2["id"]
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE playback_overrides SET starts_at = now() - interval '2 hours',"
                 " ends_at = now() - interval '1 minute' WHERE id = :i"), {"i": ov2["id"]},
        )  # fmt: skip
    await engine.dispose()
    cur = (await playback(admin, p))[w["alone"]]
    assert cur["source"] == "schedule"  # échu : plus actif même avant le balayage
    events = await OverrideSweeper(rt.maker, rt.bus, rt.schemas).sweep()
    assert [e.payload["override_id"] for e in events] == [ov2["id"]]
    got = (await admin.get(f"{p}/api/v1/overrides/{ov2['id']}")).json()
    assert got["status"] == "ended" and got["end_reason"] == "expired"
    assert (await admin.get(f"{p}/api/v1/overrides?active=true")).json() == []


async def test_override_relights_scheduled_off_by_default(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    w = await world(admin, p)
    rt = app.state.runtime
    screen = UUID(w["alone"])
    today = datetime.now(UTC).date()
    async with rt.maker.begin() as s:  # plage hors service prévue : toute la journée, +-1 jour
        s.add(
            PowerException(
                scope_level="screen", scope_id=screen, starts_on=today - timedelta(days=1),
                ends_on=today + timedelta(days=2), state="off", label="x",
            )
        )  # fmt: skip
    async with rt.maker() as s:
        base_plan = await PowerPlanBuilder(s, rt.schemas).for_screen(screen)
    assert all(x.state == "off" for x in base_plan.slots)
    ov = (
        await create(admin, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=60)
    ).json()  # fmt: skip
    ends = datetime.fromisoformat(ov["ends_at"])
    starts = datetime.fromisoformat(ov["starts_at"])
    async with rt.maker() as s:
        plan = await PowerPlanBuilder(s, rt.schemas).for_screen(screen)
    # rallumé pendant le forçage, aucun « off » ne recouvre l'intervalle (agent : premier off)
    assert not any(x.state == "off" and x.start < ends and x.end > starts for x in plan.slots)
    probe_on = starts + timedelta(seconds=1)
    assert any(x.state == "on" and x.start <= probe_on < x.end for x in plan.slots)
    assert plan.slots[-1].state == "off" and plan.slots[-1].start == ends.astimezone(UTC)
    # option « respecter le hors service prévu » : reste éteint
    await admin.post(f"{p}/api/v1/overrides/{ov['id']}/stop", headers=SAME)
    ov2 = (
        await create(admin, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=60, respect_scheduled_off=True)
    ).json()  # fmt: skip
    assert ov2["respect_scheduled_off"] is True
    async with rt.maker() as s:
        plan2 = await PowerPlanBuilder(s, rt.schemas).for_screen(screen)
    probe = datetime.now(UTC) + timedelta(minutes=10)
    assert all(x.state == "off" for x in plan2.slots if x.start <= probe < x.end)


async def test_config_default_respect_with_provenance(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    w = await world(admin, p)
    api = f"{p}/api/v1/config"
    r = await admin.get(f"{api}/screen/{w['alone']}/core")
    v = r.json()["values"]
    assert v["override.default_duration_minutes"]["value"] == 60
    assert v["override.default_duration_minutes"]["origin"]["level"] == "default"
    assert v["override.respect_scheduled_off"]["value"] is False
    r = await admin.put(
        f"{api}/site/{w['site']}/core/override.respect_scheduled_off",
        json={"value": True},
        headers=SAME,
    )
    assert r.status_code == 204, r.text
    ov = (
        await create(admin, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=5)
    ).json()  # fmt: skip
    assert ov["respect_scheduled_off"] is True  # valeur résolue héritée du site
    v = (await admin.get(f"{api}/screen/{w['alone']}/core")).json()["values"]
    assert v["override.respect_scheduled_off"]["origin"]["level"] == "site"
    bad = await admin.put(
        f"{api}/screen/{w['alone']}/core/override.retention_days", json={"value": 5}, headers=SAME
    )
    assert bad.status_code == 422  # portée organisation seulement


async def test_preview(admin: AsyncClient, app: FastAPI, p: str) -> None:
    w = await world(admin, p)
    first = (
        await create(admin, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=60)
    ).json()  # fmt: skip
    body = {"target_type": "site", "target_id": w["site"], "playlist_id": w["pl_force"],
            "duration_minutes": 30}  # fmt: skip
    r = await admin.post(f"{p}/api/v1/overrides/preview", json=body, headers=SAME)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["screens_total"] == 2 and out["screens_in_scheduled_off"] == 0
    assert out["screens_offline"] == 0 and out["superseded_override_ids"] == [first["id"]]
    assert "ends_at" in out
    assert len((await admin.get(f"{p}/api/v1/overrides")).json()) == 1  # aucun effet
    r = await admin.post(f"{p}/api/v1/overrides/preview", json={**body, "kind": "off",
                         "playlist_id": None}, headers=SAME)  # fmt: skip
    assert r.status_code == 200


async def _service(app: FastAPI, now: datetime) -> tuple[OverrideService, Any]:
    rt = app.state.runtime
    session = rt.maker()
    s = await session.__aenter__()
    actor = await _actor(s)
    return OverrideService(s, None, actor, rt.schemas, rt.registry.content_item_schema,
                           clock=lambda: now), session  # fmt: skip


async def _actor(s: Any) -> Any:
    from vds_server.core.auth import Principal

    uid = (await s.execute(text("SELECT id FROM user_account LIMIT 1"))).scalar_one()
    return Principal(user_id=uid, provider="auth.local", email=None, display_name="x",
                     role="admin", locale=None)  # fmt: skip


@pytest.mark.parametrize(
    ("now_utc", "expected_end_utc"),
    [
        ("2026-03-28T10:00:00+00:00", "2026-03-28T23:00:00+00:00"),  # minuit Paris (UTC+1)
        (
            "2026-03-28T23:30:00+00:00",
            "2026-03-29T22:00:00+00:00",
        ),  # 00:30 Paris le 29 : veille du DST
        ("2026-10-24T22:30:00+00:00", "2026-10-25T23:00:00+00:00"),  # jour de 25 h (fin du DST)
    ],
)
async def test_end_of_day_in_site_timezone(
    admin: AsyncClient, app: FastAPI, p: str, now_utc: str, expected_end_utc: str
) -> None:
    w = await world(admin, p)
    svc, session = await _service(app, datetime.fromisoformat(now_utc))
    try:
        out = await svc.create(
            sc.OverrideCreate(
                target_type="screen",
                target_id=UUID(w["alone"]),
                playlist_id=UUID(w["pl_force"]),
                until="end_of_day",
            )  # fmt: skip
        )
    finally:
        await session.__aexit__(None, None, None)
    assert out.ends_at == datetime.fromisoformat(expected_end_utc)


async def test_end_of_day_uses_target_site_timezone(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    w = await world(admin, p)
    r = await admin.put(
        f"{p}/api/v1/config/site/{w['site']}/core/timezone",
        json={"value": "America/New_York"},
        headers=SAME,
    )
    assert r.status_code == 204
    svc, session = await _service(app, datetime(2026, 1, 10, 12, 0, tzinfo=UTC))
    try:
        out = await svc.create(
            sc.OverrideCreate(
                target_type="site",
                target_id=UUID(w["site"]),
                playlist_id=UUID(w["pl_force"]),
                until="end_of_day",
            )  # fmt: skip
        )
    finally:
        await session.__aexit__(None, None, None)
    assert out.ends_at == datetime(2026, 1, 11, 5, 0, tzinfo=UTC)  # minuit à New York (UTC-5)


async def test_events_audit_expiry_and_purge(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    w = await world(admin, p)
    rt = app.state.runtime
    seen: list[Event] = []

    async def on(event: Event) -> None:
        seen.append(event)

    for name in ("override.created", "override.updated", "override.stopped", "override.expired"):
        rt.bus.subscribe(name, on)
    media = await make_media(migrated_db_url)
    a = (
        await create(
            admin, p, target_type="screen", target_id=w["alone"], duration_minutes=30,
            content={"content_type": "content.image", "props": {"src": str(media)}},
        )
    ).json()  # fmt: skip
    b = (
        await create(admin, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=30)
    ).json()  # fmt: skip
    await admin.patch(
        f"{p}/api/v1/overrides/{b['id']}",
        json={"ends_at": (datetime.fromisoformat(b["ends_at"]) + timedelta(hours=1)).isoformat()},
        headers=SAME,
    )
    await admin.post(f"{p}/api/v1/overrides/{b['id']}/stop", headers=SAME)
    names = [e.name for e in seen]
    assert names == ["override.created", "override.created", "override.updated", "override.stopped"]
    assert seen[0].payload["version"] == 1 and seen[0].payload["override_id"] == a["id"]
    assert seen[3].payload["ended_by"] and seen[3].payload["target_id"] == w["alone"]
    # expiration constatée par le balayeur : aucune poussée, un événement, un audit système
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE playback_overrides SET starts_at = now() - interval '2 hours',"
                 " ends_at = now() - interval '1 minute' WHERE id = :i"), {"i": a["id"]},
        )  # fmt: skip
    sweeper = OverrideSweeper(rt.maker, rt.bus, rt.schemas)
    events = await sweeper.sweep()
    assert [e.name for e in events] == ["override.expired"] and events[0].payload["version"] == 1
    assert (await sweeper.sweep()) == []
    got = (await admin.get(f"{p}/api/v1/overrides/{a['id']}")).json()
    assert got["status"] == "ended" and got["end_reason"] == "expired"
    actions = await audit_actions(migrated_db_url)
    assert {"override.create", "override.update", "override.stop", "override.expire"} <= set(
        actions
    )
    # purge : rétention de 90 j par défaut ; l'audit reste
    assert await sweeper.purge() == 0
    async with engine.begin() as c:
        await c.execute(
            text("UPDATE playback_overrides SET ended_at = now() - interval '91 days',"
                 " ends_at = now() - interval '91 days', starts_at = now() - interval '92 days'"
                 " WHERE id = :i"), {"i": a["id"]},
        )  # fmt: skip
    assert await sweeper.purge() == 1
    async with rt.maker() as s:
        assert await s.get(PlaybackOverride, UUID(a["id"])) is None
        assert await s.get(Playlist, UUID(a["playlist_id"])) is None  # éphémère purgée avec lui
        assert await s.get(Playlist, UUID(w["pl_force"])) is not None
    assert "override.create" in await audit_actions(migrated_db_url)
    await engine.dispose()


async def test_in_use_playlist_cannot_be_deleted(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    await create(
        admin, p, target_type="screen", target_id=w["alone"], playlist_id=w["pl_force"],
        duration_minutes=5,
    )  # fmt: skip
    r = await admin.delete(f"{p}/api/v1/playlists/{w['pl_force']}", headers=SAME)
    assert r.status_code == 409


async def test_editor_cannot_stop_or_extend_admin_off_or_org(
    app: FastAPI, admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    w = await world(admin, p)
    async with app.state.runtime.maker() as s:
        org = (await s.execute(text("SELECT id FROM organization"))).scalar_one()
    off = (await create(admin, p, target_type="screen", target_id=w["alone"], kind="off",
                        duration_minutes=30)).json()  # fmt: skip
    orgo = (await create(admin, p, target_type="organization", target_id=str(org),
                         playlist_id=w["pl_force"], duration_minutes=30)).json()  # fmt: skip
    end = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    for o in (off, orgo):
        r = await editor.patch(f"{p}/api/v1/overrides/{o['id']}", json={"ends_at": end},
                               headers=SAME)  # fmt: skip
        assert r.status_code == 403 and r.json()["code"] == "override.forbidden_kind"
        r = await editor.post(f"{p}/api/v1/overrides/{o['id']}/stop", headers=SAME)
        assert r.status_code == 403
        assert (await admin.get(f"{p}/api/v1/overrides/{o['id']}")).json()["status"] == "active"
    mine = (await create(editor, p, target_type="screen", target_id=w["in_group"],
                         playlist_id=w["pl_force"], duration_minutes=30)).json()  # fmt: skip
    r = await editor.post(f"{p}/api/v1/overrides/{mine['id']}/stop", headers=SAME)
    assert r.status_code == 200


async def test_admin_off_beats_editor_playlist(
    admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    w = await world(admin, p)
    off = (await create(admin, p, target_type="screen", target_id=w["alone"], kind="off",
                        duration_minutes=30)).json()  # fmt: skip
    # forçage d'éditeur créé après, priorité maximale : l'extinction gagne quand même
    r = await create(editor, p, target_type="screen", target_id=w["alone"],
                     playlist_id=w["pl_force"], duration_minutes=30, priority=1000)  # fmt: skip
    assert r.status_code == 201
    cur = (await playback(admin, p))[w["alone"]]
    assert cur["override_id"] == off["id"] and cur["playlist_name"] is None


def test_resolver_off_beats_playlist_same_nature_last_wins() -> None:
    from datetime import date

    from vds_server.diffusion.resolver import OverrideSpec, resolve
    from vds_server.power.resolver import apply_overrides
    from vds_server.power.resolver import resolve as presolve

    tz = UTC
    scr = uuid4()
    chain = {"screen": scr}
    t0 = datetime(2026, 11, 2, 8, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    def spec(pl: UUID | None, prio: int, created: int) -> OverrideSpec:
        return OverrideSpec(uuid4(), "screen", scr, t0, t1, pl, prio,
                            t0 + timedelta(seconds=created))  # fmt: skip

    off, pl_old, pl_new = spec(None, 0, 0), spec(uuid4(), 5, 1), spec(uuid4(), 1000, 2)
    slots = resolve(chain, [], tz, date(2026, 11, 2), [pl_old, pl_new, off])
    assert [s.override_id for s in slots if s.override_id] == [off.id]
    slots = resolve(chain, [], tz, date(2026, 11, 2), [pl_old, pl_new])
    assert [s.override_id for s in slots if s.override_id] == [pl_new.id]  # dernier créé gagne
    power = presolve(None, [], [], tz, date(2026, 11, 2))
    out = apply_overrides(power, chain, [pl_new, off], tz)
    assert any(s.state == "off" and s.start == t0 and s.end == t1 for s in out)


async def test_quota_defer_pagination(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    r = await admin.put(
        f"{p}/api/v1/config/site/{w['site']}/core/override.max_active_per_target",
        json={"value": 2}, headers=SAME,
    )  # fmt: skip
    assert r.status_code == 204, r.text
    tgt = {"target_type": "screen", "target_id": w["alone"], "playlist_id": w["pl_force"]}
    for _ in range(2):
        assert (await create(admin, p, **tgt, duration_minutes=5)).status_code == 201
    r = await create(admin, p, **tgt, duration_minutes=5)
    assert r.status_code == 409 and r.json()["code"] == "override.too_many_active"
    far = (datetime.now(UTC) + timedelta(days=31)).isoformat()
    other = {**tgt, "target_id": w["in_group"]}
    r = await create(admin, p, **other, starts_at=far, duration_minutes=5)
    assert r.status_code == 422 and r.json()["code"] == "override.start_too_far"
    page = await admin.get(f"{p}/api/v1/overrides?limit=1&offset=1")
    assert page.status_code == 200 and len(page.json()) == 1
    assert (await admin.get(f"{p}/api/v1/overrides?limit=0")).status_code == 422
    assert len((await admin.get(f"{p}/api/v1/overrides?offset=5")).json()) == 0


async def test_preview_superseded_and_purge_audit_ids(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    w = await world(admin, p)
    a = (await create(admin, p, target_type="screen", target_id=w["alone"],
                      playlist_id=w["pl_force"], duration_minutes=30)).json()  # fmt: skip
    g = (await create(admin, p, target_type="group", target_id=w["group"],
                       playlist_id=w["pl_force"], duration_minutes=30)).json()  # fmt: skip
    r = await admin.post(f"{p}/api/v1/overrides/preview", json={
        "target_type": "site", "target_id": w["site"], "playlist_id": w["pl_force"],
        "duration_minutes": 10}, headers=SAME)  # fmt: skip
    assert r.status_code == 200
    assert sorted(r.json()["superseded_override_ids"]) == sorted([a["id"], g["id"]])
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text("UPDATE playback_overrides SET starts_at = now() - interval '93 days',"
                             " ended_at = now() - interval '91 days',"
                             " ends_at = now() - interval '91 days'"))  # fmt: skip
    sweeper = OverrideSweeper(app.state.runtime.maker, app.state.runtime.bus,
                              app.state.runtime.schemas)  # fmt: skip
    assert await sweeper.purge() == 2
    async with engine.begin() as c:
        det = (
            await c.execute(text("SELECT details FROM audit_log WHERE action = 'override.purge'"))
        ).scalar_one()
    assert sorted(det["ids"]) == sorted([a["id"], g["id"]]) and det["count"] == 2
    await engine.dispose()


async def test_preview_superseded_follows_resolver(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    w = await world(admin, p)
    high = (await create(admin, p, target_type="screen", target_id=w["alone"],
                         playlist_id=w["pl_force"], duration_minutes=30,
                         priority=50)).json()  # fmt: skip
    low = (await create(admin, p, target_type="group", target_id=w["group"],
                        playlist_id=w["pl_force"], duration_minutes=30)).json()  # fmt: skip
    r = await admin.post(f"{p}/api/v1/overrides/preview", json={
        "target_type": "site", "target_id": w["site"], "playlist_id": w["pl_force"],
        "duration_minutes": 10, "priority": 10}, headers=SAME)  # fmt: skip
    assert r.status_code == 200
    assert r.json()["superseded_override_ids"] == [low["id"]]  # le plus prioritaire reste
    assert high["id"] not in r.json()["superseded_override_ids"]


async def test_preview_screen_cap(
    admin: AsyncClient, p: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = await world(admin, p)
    monkeypatch.setattr("vds_server.overrides.service.MAX_PREVIEW_SCREENS", 1)
    body = {"target_type": "site", "target_id": w["site"], "playlist_id": w["pl_force"],
            "duration_minutes": 30}  # fmt: skip
    r = await admin.post(f"{p}/api/v1/overrides/preview", json=body, headers=SAME)
    assert r.status_code == 422 and r.json()["code"] == "override.preview_too_many_screens"
