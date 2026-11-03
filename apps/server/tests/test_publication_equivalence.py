"""Non-régression de la résolution : lire les instantanés ne change rien à ce que jouent les écrans.

Point dur du chantier B : tout écart change ce que jouent les écrans. Une même base riche (arbre
organisation/site/groupe/écrans, programmations à règles, période, priorité, désactivée, mise en
page, forçages d'écran et de zone, playlists à expiration, éléments désactivés) est lue deux fois :
par l'ancien chemin (copie de travail, `legacy_inputs`) et par le nouveau (instantanés). Les
documents player, les révisions et les médias autorisés doivent être identiques.
"""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import legacy_inputs as legacy
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, _diffusion_clean, make_media, mk_playlist  # noqa: F401
from test_fleet_playback import screen_of, site_of
from test_layouts_api import _layout_clean, mk_layout, ov, zone  # noqa: F401
from test_pairing import _pairing_clean  # noqa: F401
from test_player_schedule import _entries, mk_item  # noqa: F401
from test_publication import sql
from vds_server.db.models_publication import Publication
from vds_server.diffusion import layout_content
from vds_server.diffusion.layout_compose import ResolvedDefaults
from vds_server.player import schedule as ps
from vds_server.player.schedule import ScheduleBuilder
from vds_server.publication import snapshots as snap


async def sched(c: AsyncClient, p: str, **body: Any) -> str:
    r = await c.post(f"{p}/api/v1/schedules", json={"name": "S", **body}, headers=SAME)
    assert r.status_code == 201, r.text
    assert r.json()["publication"]["state"] == "published", r.json()["publication"]
    return str(r.json()["id"])


async def world(admin: AsyncClient, p: str, url: str) -> list[str]:
    """Retourne les écrans du scénario."""
    site = await site_of(admin, p, "a")
    g1 = (
        await admin.post(f"{p}/api/v1/sites/{site}/groups", json={"name": "G1"}, headers=SAME)
    ).json()["id"]
    s1 = await screen_of(admin, p, site, "S1", g1)
    s2 = await screen_of(admin, p, site, "S2", g1)
    s3 = await screen_of(admin, p, site, "S3")
    m1, m2, m3 = [await make_media(url, title=t) for t in ("un", "deux", "trois")]
    p1 = await mk_playlist(admin, p, "P1")
    for m in (m1, m2, m1):
        await mk_item(admin, p, p1, m)
    items = (await admin.get(f"{p}/api/v1/playlists/{p1}")).json()["items"]
    await admin.patch(
        f"{p}/api/v1/playlists/{p1}/items/{items[1]['id']}", json={"enabled": False}, headers=SAME
    )
    await admin.patch(
        f"{p}/api/v1/playlists/{p1}/items/{items[2]['id']}",
        json={"duration_ms": None},
        headers=SAME,
    )
    p2 = await mk_playlist(admin, p, "P2")
    await mk_item(admin, p, p2, m3)
    soon = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    await admin.patch(f"{p}/api/v1/playlists/{p2}", json={"expires_at": soon}, headers=SAME)
    p3 = await mk_playlist(admin, p, "P3-vide")
    window = {"active_from": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
              "active_until": (datetime.now(UTC) + timedelta(hours=9)).isoformat()}  # fmt: skip
    lay = await mk_layout(
        admin, p, "L1",
        zone("main", playlist_id=p1, default_item_duration_ms=7000),
        zone("banner", y=0.9, h=0.1, z=2, fit="cover", playlist_id=p2, **window),
        background_color="#112233", background_media_id=str(m1),
    )  # fmt: skip
    rules = [{"weekdays": 31, "start": "08:00", "end": "19:00"},
             {"weekdays": 96, "start": "10:00", "end": "24:00"}]  # fmt: skip
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    await sched(admin, p, playlist_id=p3, target_type="organization", target_id=org)
    await sched(admin, p, layout_id=lay["id"], target_type="site", target_id=site, priority=1)
    await sched(admin, p, playlist_id=p1, target_type="group", target_id=g1, rules=rules)
    today = datetime.now(UTC).date()
    period = {
        "starts_on": str(today - timedelta(days=1)),
        "ends_on": str(today + timedelta(days=3)),
    }
    await sched(admin, p, playlist_id=p2, target_type="screen", target_id=s1, priority=5, **period)
    off = await sched(admin, p, playlist_id=p1, target_type="screen", target_id=s3, priority=9)
    await admin.patch(f"{p}/api/v1/schedules/{off}", json={"enabled": False}, headers=SAME)
    await sched(admin, p, layout_id=lay["id"], target_type="screen", target_id=s3, priority=2)
    r = await ov(admin, p, target_type="screen", target_id=s2, playlist_id=p2, duration_minutes=30)
    assert r.status_code == 201, r.text
    r = await ov(admin, p, target_type="screen", target_id=s3, playlist_id=p1,
                 zone_ids=["banner"], duration_minutes=45)  # fmt: skip
    assert r.status_code == 201, r.text
    return [s1, s2, s3]


async def docs(app: FastAPI, screens: list[str], now: datetime) -> dict[str, Any]:
    rt = app.state.runtime

    async def tz(_: UUID) -> str:
        return "Europe/Paris"

    async def defaults(_: UUID) -> ResolvedDefaults:
        return ResolvedDefaults(max_exclusive_elements=2)

    out: dict[str, Any] = {}
    async with rt.maker() as s:
        builder = ScheduleBuilder(
            s, rt.registry.content_item_schema, rt.registry.content_entry, tz, defaults_of=defaults
        )
        for screen in screens:
            out[screen] = await builder.build(UUID(screen), now)
            out["media:" + screen] = sorted(map(str, await ps.screen_media_ids(s, UUID(screen))))
            out["media+expired:" + screen] = sorted(
                map(str, await ps.screen_media_ids(s, UUID(screen), include_expired=True))
            )
    return out


async def test_documents_identical_to_working_copy_reads(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    migrated_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screens = await world(admin, p, migrated_db_url)
    now = datetime.now(UTC)
    new = await docs(app, screens, now)
    assert any(d["slots"] for d in new.values() if isinstance(d, dict))
    assert any(d["playlists"] for d in new.values() if isinstance(d, dict))
    assert any(new["media:" + s] for s in screens)
    with monkeypatch.context() as m:
        m.setattr(ps, "_fetch_specs", legacy.fetch_specs)
        m.setattr(ps, "_fetch_override_rows", legacy.fetch_override_rows)
        m.setattr(ps, "load_layouts", legacy.load_layouts)
        m.setattr(layout_content, "load_layouts", legacy.load_layouts)
        m.setattr(ScheduleBuilder, "_playlists", legacy.playlists)
        m.setattr(ps, "screen_media_ids", legacy.screen_media_ids)
        old = await docs(app, screens, now)  # `docs` appelle ps.screen_media_ids : version d'avant
    assert new.keys() == old.keys()
    for key in new:
        assert new[key] == old[key], key
    assert new[screens[0]]["revision"] == old[screens[0]]["revision"]


async def test_oracle_detects_working_copy_divergence(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    migrated_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garde-fou du test précédent : une copie de travail modifiée en SQL (sans publication)
    change l'ancien chemin mais pas le nouveau."""
    screens = await world(admin, p, migrated_db_url)
    now = datetime.now(UTC)
    before = await docs(app, screens, now)
    await sql(migrated_db_url, "UPDATE playlist_item SET enabled = false")
    await sql(migrated_db_url, "UPDATE schedule SET priority = priority + 3")
    assert await docs(app, screens, now) == before  # le publié ne bouge pas
    with monkeypatch.context() as m:
        m.setattr(ps, "_fetch_specs", legacy.fetch_specs)
        m.setattr(ScheduleBuilder, "_playlists", legacy.playlists)
        assert await docs(app, screens, now) != before


async def test_snapshot_roundtrip_orm_dto_json_dto(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    await world(admin, p, migrated_db_url)
    async with app.state.runtime.maker() as s:
        rows = list((await s.scalars(select(Publication))).all())
        assert {r.entity_type for r in rows} == {"playlist", "layout", "schedule"}
        for row in rows:
            assert row.snapshot is not None and row.snapshot["v"] == 1
            stored = snap.load(row.entity_type, row.snapshot)
            built = await snap.build(s, row.entity_type, row.entity_id)  # ORM -> DTO
            assert built == stored  # DTO -> JSON -> DTO identique à l'ORM courant
            assert snap.dump(stored) == row.snapshot
            assert snap.load(row.entity_type, snap.dump(built)) == built
