"""Mises en page : API /api/v1/layouts, cible layout (programmation, forçage), forçage de zone,
document player 1.9, aperçu. Exécuté aux deux VDS_BASE_PATH (fixture `p`)."""
# ruff: noqa: F811

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, audit_actions, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, make_media, mk_playlist  # noqa: F401
from test_fleet_playback import playback, schedule, screen_of, site_of
from vds_sdk import Event
from vds_server.config.schemas import CORE_SCHEMA
from vds_server.diffusion.layout_compose import ResolvedDefaults
from vds_server.player.schedule import ScheduleBuilder

GHOST = "00000000-0000-0000-0000-000000000009"


@pytest.fixture(autouse=True)
async def _layout_clean(migrated_db_url: str) -> AsyncIterator[None]:
    async def truncate() -> None:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            await c.execute(
                text(
                    "TRUNCATE layout, schedule, playback_overrides, playlist, media,"
                    " audit_log CASCADE"
                )
            )
        await engine.dispose()

    await truncate()
    yield
    await truncate()


def zone(key: str = "main", **kw: Any) -> dict[str, Any]:
    return {"key": key, "name": key.title(), "x": 0, "y": 0, "w": 1, "h": 1, **kw}


def lbody(name: str = "Accueil", *zones: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"name": name, "orientation": "landscape", "zones": list(zones) or [zone()], **kw}


async def mk_layout(
    c: AsyncClient, p: str, name: str = "Accueil", *zones: dict[str, Any], **kw: Any
) -> dict[str, Any]:
    r = await c.post(f"{p}/api/v1/layouts", json=lbody(name, *zones, **kw), headers=SAME)
    assert r.status_code == 201, r.text
    return dict(r.json())


async def build_doc(app: FastAPI, screen: str) -> dict[str, Any]:
    rt = app.state.runtime

    async def tz(_: UUID) -> str:
        return "Europe/Paris"

    async def defaults(_: UUID) -> ResolvedDefaults:
        return ResolvedDefaults(max_exclusive_elements=2)

    async with rt.maker() as s:
        return await ScheduleBuilder(
            s,
            rt.registry.content_item_schema,
            rt.registry.content_entry,
            tz,
            defaults_of=defaults,
        ).build(UUID(screen))


async def ov(c: AsyncClient, p: str, **body: Any) -> Any:
    return await c.post(f"{p}/api/v1/overrides", json=body, headers=SAME)


async def sched_layout(c: AsyncClient, p: str, layout_id: str, screen: str) -> Any:
    return await c.post(
        f"{p}/api/v1/schedules",
        json={"name": "S", "layout_id": layout_id, "target_type": "screen", "target_id": screen},
        headers=SAME,
    )


# --- CRUD ----------------------------------------------------------------------------
async def test_crud_etag_if_match_duplicate_delete(admin: AsyncClient, p: str) -> None:
    pl = await mk_playlist(admin, p, "Boucle")
    lay = await mk_layout(
        admin,
        p,
        "Hall",
        zone("bandeau", y=0.9, h=0.1, z=2, fit="cover", playlist_id=pl),
        zone("main", z=0, default_item_duration_ms=5000),
        tags=["Accueil", "accueil"],
    )
    assert lay["revision"] == 1 and lay["tags"] == ["accueil"]
    assert [z["key"] for z in lay["zones"]] == ["main", "bandeau"]  # tri (z, key)
    assert lay["zones"][1]["y"] == 0.9 and lay["zones"][1]["playlist_id"] == pl
    url = f"{p}/api/v1/layouts/{lay['id']}"
    got = await admin.get(url)
    assert got.status_code == 200 and got.headers["etag"] == '"1"'
    body = lbody("Hall v2", zone("main", w=0.5), tags=["accueil"])
    assert (await admin.put(url, json=body, headers=SAME)).status_code == 428
    r = await admin.put(url, json=body, headers={**SAME, "If-Match": "abc"})
    assert r.status_code == 422 and r.json()["code"] == "layout.if_match_invalid"
    r = await admin.put(url, json=body, headers={**SAME, "If-Match": '"1"'})
    assert r.status_code == 200 and r.json()["revision"] == 2 and r.headers["etag"] == '"2"'
    assert [z["key"] for z in r.json()["zones"]] == ["main"]  # remplacement complet
    stale = await admin.put(url, json=body, headers={**SAME, "If-Match": "1"})
    assert stale.status_code == 412 and stale.json()["code"] == "layout.revision_conflict"
    dup = await admin.post(f"{url}/duplicate", headers=SAME)
    assert dup.status_code == 201 and dup.json()["name"] == "Hall v2 (copie)"
    assert dup.json()["revision"] == 1 and dup.json()["id"] != lay["id"]
    again = await admin.post(f"{url}/duplicate", headers=SAME)
    assert again.json()["name"] == "Hall v2 (copie) 2"
    page = (await admin.get(f"{p}/api/v1/layouts")).json()
    assert page["total"] == 3 and {i["zone_count"] for i in page["items"]} == {1}
    by_name = (await admin.get(f"{p}/api/v1/layouts?q=copie&orientation=landscape")).json()
    assert by_name["total"] == 2
    assert (await admin.get(f"{p}/api/v1/layouts?orientation=portrait")).json()["total"] == 0
    assert (await admin.get(f"{p}/api/v1/layouts?tag=accueil")).json()["total"] == 3
    assert (await admin.delete(url, headers=SAME)).status_code == 204
    assert (await admin.get(url)).status_code == 404
    clash = await admin.post(f"{p}/api/v1/layouts", json=lbody("Hall v2 (copie)"), headers=SAME)
    assert clash.status_code == 409 and clash.json()["code"] == "layout.name_conflict"


async def test_validation_codes(admin: AsyncClient, p: str, migrated_db_url: str) -> None:
    api = f"{p}/api/v1/layouts"

    async def code(body: dict[str, Any]) -> tuple[int, str | None]:
        r = await admin.post(api, json=body, headers=SAME)
        return r.status_code, r.json().get("code")

    assert await code(lbody("a", zone(x=0.6, w=0.5))) == (422, "layout.zone_out_of_bounds")
    assert await code(lbody("a", zone(y=0.9, h=0.2))) == (422, "layout.zone_out_of_bounds")
    assert await code(lbody("a", zone("k"), zone("k"))) == (422, "layout.zone_key_duplicate")
    many = lbody("a", *[zone(f"z{i}") for i in range(13)])
    assert await code(many) == (422, "layout.too_many_zones")
    assert await code(lbody("a", zone(playlist_id=GHOST))) == (422, "layout.playlist_not_found")
    video = await make_media(migrated_db_url, kind="video")
    assert await code(lbody("a", background_media_id=str(video))) == (
        422,
        "layout.background_not_image",
    )
    assert await code(lbody("a", background_media_id=GHOST)) == (422, "layout.media_not_found")
    image = await make_media(migrated_db_url)
    assert (await code(lbody("a", background_media_id=str(image))))[0] == 201
    for bad in (
        zone("Bad Key"),
        zone(w=0),
        zone(z=100),
        zone(fit="x"),
        zone(default_item_duration_ms=10),
    ):
        assert (await code(lbody("b", bad)))[0] == 422
    assert (await code({**lbody("b"), "zones": []}))[0] == 422
    assert (await code(lbody("b", background_color="red")))[0] == 422
    now = datetime.now(UTC)
    window = zone(active_from=now.isoformat(), active_until=(now - timedelta(hours=1)).isoformat())
    assert (await code(lbody("c", window)))[0] == 422


def test_settings_declared_in_core_schema() -> None:
    props = CORE_SCHEMA["properties"]
    assert props["layout.max_zones"]["default"] == 12
    assert props["layout.max_zones"]["x-vds-scopes"] == ["organization"]
    assert props["player.max_exclusive_elements"]["default"] == 1
    assert props["player.max_exclusive_elements"]["maximum"] == 4
    assert props["default_item_duration_ms"]["default"] == 10000


async def test_access_and_same_origin(
    admin: AsyncClient, editor: AsyncClient, app: FastAPI, p: str
) -> None:
    async with new_client(app) as other:
        assert (await other.get(f"{p}/api/v1/layouts")).status_code == 401
    r = await admin.post(
        f"{p}/api/v1/layouts", json=lbody(), headers={"Origin": "https://evil.test"}
    )
    assert r.status_code == 403
    lay = await mk_layout(editor, p, "Par l'éditeur")
    assert (await editor.get(f"{p}/api/v1/layouts/{lay['id']}")).status_code == 200


# --- cible layout : programmation ----------------------------------------------------
async def test_schedule_xor_and_in_use(admin: AsyncClient, p: str) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    pl = await mk_playlist(admin, p)
    lay = await mk_layout(admin, p)
    base_body = {"name": "S", "target_type": "screen", "target_id": scr}
    url = f"{p}/api/v1/schedules"
    both = await admin.post(
        url, json={**base_body, "playlist_id": pl, "layout_id": lay["id"]}, headers=SAME
    )
    assert both.status_code == 422 and both.json()["code"] == "schedule.content_target_ambiguous"
    none = await admin.post(url, json=base_body, headers=SAME)
    assert none.json()["code"] == "schedule.content_target_ambiguous"
    ghost = await admin.post(url, json={**base_body, "layout_id": GHOST}, headers=SAME)
    assert ghost.status_code == 422 and ghost.json()["code"] == "schedule.layout_not_found"
    ok = await admin.post(url, json={**base_body, "layout_id": lay["id"]}, headers=SAME)
    assert ok.status_code == 201, ok.text
    sch = ok.json()
    assert sch["playlist_id"] is None and sch["layout_id"] == lay["id"] and sch["warnings"] == []
    patched = await admin.patch(f"{url}/{sch['id']}", json={"playlist_id": pl}, headers=SAME)
    assert patched.json()["playlist_id"] == pl and patched.json()["layout_id"] is None
    both_p = await admin.patch(
        f"{url}/{sch['id']}", json={"playlist_id": pl, "layout_id": lay["id"]}, headers=SAME
    )
    assert both_p.status_code == 422
    await admin.patch(f"{url}/{sch['id']}", json={"layout_id": lay["id"]}, headers=SAME)
    r = await admin.delete(f"{p}/api/v1/layouts/{lay['id']}", headers=SAME)
    assert r.status_code == 409 and r.json()["code"] == "layout.in_use"
    assert r.json()["params"]["schedules"] == [sch["id"]]
    usage = (await admin.get(f"{p}/api/v1/layouts/{lay['id']}/usage")).json()
    assert [s["id"] for s in usage["schedules"]] == [sch["id"]] and usage["screens_total"] == 1
    assert (await admin.delete(f"{url}/{sch['id']}", headers=SAME)).status_code == 204
    assert (await admin.delete(f"{p}/api/v1/layouts/{lay['id']}", headers=SAME)).status_code == 204


async def test_orientation_mismatch_warning_is_not_blocking(admin: AsyncClient, p: str) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")  # paysage par défaut
    lay = await mk_layout(admin, p, "Vertical", orientation="portrait")
    r = await sched_layout(admin, p, lay["id"], scr)
    assert r.status_code == 201
    assert r.json()["warnings"] == ["layout_orientation_mismatch"]
    assert r.json()["screens_orientation_mismatch"] == 1
    usage = (await admin.get(f"{p}/api/v1/layouts/{lay['id']}/usage")).json()
    assert usage["screens_orientation_mismatch"] == 1
    o = await ov(
        admin, p, target_type="screen", target_id=scr, layout_id=lay["id"], duration_minutes=10
    )
    assert o.status_code == 201 and o.json()["warnings"] == ["layout_orientation_mismatch"]


# --- purge ------------------------------------------------------------------------------
async def test_playlist_and_media_purge_detach_and_bump_revision(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    pl = await mk_playlist(admin, p, "Zone")
    media = await make_media(migrated_db_url)
    lay = await mk_layout(admin, p, "L", zone("a", playlist_id=pl), background_media_id=str(media))
    seen: list[Event] = []

    async def on(e: Event) -> None:
        seen.append(e)

    app.state.runtime.bus.subscribe("layout.updated", on)
    assert (await admin.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)).status_code == 204
    got = (await admin.get(f"{p}/api/v1/layouts/{lay['id']}")).json()
    assert got["zones"][0]["playlist_id"] is None and got["revision"] == 2
    assert (await admin.delete(f"{p}/api/v1/media/{media}", headers=SAME)).status_code == 204
    got = (await admin.get(f"{p}/api/v1/layouts/{lay['id']}")).json()
    assert got["background_media_id"] is None and got["revision"] == 3
    assert [e.payload["revision"] for e in seen] == [2, 3]


# --- document player ------------------------------------------------------------------
async def test_player_document_with_layout_background_and_zone_override(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    main_pl, banner_pl, forced_pl = [await mk_playlist(admin, p, n) for n in ("M", "B", "F")]
    bg = await make_media(migrated_db_url, title="Fond")
    lay = await mk_layout(
        admin,
        p,
        "Hall",
        zone("main", playlist_id=main_pl),
        zone("bandeau", y=0.9, h=0.1, z=5, playlist_id=banner_pl, fit="cover"),
        zone("vide", x=0.5, w=0.5, h=0.5, z=3),
        background_color="#102030",
        background_media_id=str(bg),
    )
    assert (await sched_layout(admin, p, lay["id"], scr)).status_code == 201
    doc = await build_doc(app, scr)
    (slot,) = [s for s in doc["slots"] if s["kind"] == "play"]
    zones = slot["layout"]["zones"]
    assert [z["id"] for z in zones] == ["main", "vide", "bandeau"]
    assert slot["layout"]["id"] == lay["id"] and slot["layout"]["revision"] == 1
    assert slot["layout"]["orientation"] == "landscape"
    bgd = slot["layout"]["background"]
    assert bgd["color"] == "#102030" and bgd["asset"] == f"media/{bg}/original"
    assert bgd["asset"] in doc["assets"]
    assert slot["source"]["layout_id"] == lay["id"] and slot["source"]["kind"] == "schedule"
    assert doc["playlists"]["pl_empty"] == {"items": []}
    assert zones[1]["playlist"] == "pl_empty"
    assert all(z["playlist"] in doc["playlists"] for z in zones)
    assert doc["player"] == {"max_exclusive_elements": 2}
    rev = doc["revision"]
    # forçage de zone : créneau non découpé, frise sur la zone ciblée seulement
    r = await ov(
        admin, p, target_type="screen", target_id=scr, playlist_id=forced_pl,
        zone_ids=["bandeau"], duration_minutes=30,
    )  # fmt: skip
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["zone_ids"] == ["bandeau"] and created["warnings"] == []
    doc2 = await build_doc(app, scr)
    plays = [s for s in doc2["slots"] if s["kind"] == "play"]
    assert len(plays) == 1 and plays[0]["start"] == slot["start"] and plays[0]["end"] == slot["end"]
    by = {z["id"]: z for z in plays[0]["layout"]["zones"]}
    (w,) = by["bandeau"]["overrides"]
    assert w["override_id"] == created["id"] and w["playlist"] in doc2["playlists"]
    assert "overrides" not in by["main"] and plays[0]["source"]["kind"] == "schedule"
    assert doc2["revision"] != rev
    # /stop : la révision revient, plus de frise
    await admin.post(f"{p}/api/v1/overrides/{created['id']}/stop", headers=SAME)
    doc3 = await build_doc(app, scr)
    assert doc3["revision"] == rev
    assert all(
        "overrides" not in z
        for s in doc3["slots"]
        if s["kind"] == "play"
        for z in s["layout"]["zones"]
    )
    # layout.updated change la révision du document
    put = await admin.put(
        f"{p}/api/v1/layouts/{lay['id']}",
        json=lbody("Hall", zone("main", playlist_id=main_pl)),
        headers={**SAME, "If-Match": "1"},
    )
    assert put.status_code == 200
    doc4 = await build_doc(app, scr)
    assert doc4["revision"] != rev
    ids = [z["id"] for s in doc4["slots"] if s["kind"] == "play" for z in s["layout"]["zones"]]
    assert ids == ["main"]


async def test_playlist_target_document_unchanged(admin: AsyncClient, app: FastAPI, p: str) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    pl = await mk_playlist(admin, p)
    await schedule(admin, p, pl, "screen", scr, "S")
    rt = app.state.runtime

    async def tz(_: UUID) -> str:
        return "Europe/Paris"

    async with rt.maker() as s:
        doc = await ScheduleBuilder(
            s, rt.registry.content_item_schema, rt.registry.content_entry, tz
        ).build(UUID(scr))
    (slot,) = [x for x in doc["slots"] if x["kind"] == "play"]
    assert slot["layout"] == {
        "zones": [{"id": "main", "x": 0, "y": 0, "w": 1, "h": 1, "playlist": f"pl_{pl}"}]
    }
    assert set(slot["source"]) == {"schedule_id", "kind", "override_id"} and "player" not in doc


async def test_zone_override_absent_zone_ignored_and_not_on_dark_screen(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    pl, forced = await mk_playlist(admin, p, "A"), await mk_playlist(admin, p, "F")
    lay = await mk_layout(admin, p, "Sans bandeau", zone("main", playlist_id=pl))
    # écran sans programmation : un forçage de zone n'allume rien et est signalé
    r = await ov(
        admin, p, target_type="screen", target_id=scr, playlist_id=forced,
        zone_ids=["main"], duration_minutes=30,
    )  # fmt: skip
    assert r.status_code == 201
    assert r.json()["warnings"] == ["override_zone_missing"] and r.json()["screens_missing"] == 1
    doc = await build_doc(app, scr)
    assert not [s for s in doc["slots"] if s["kind"] == "play"]
    # mise en page programmée : `main` existe, `bandeau` non
    await sched_layout(admin, p, lay["id"], scr)
    r2 = await ov(
        admin, p, target_type="screen", target_id=scr, playlist_id=forced,
        zone_ids=["bandeau"], duration_minutes=30,
    )  # fmt: skip
    assert r2.json()["warnings"] == ["override_zone_missing"]
    doc = await build_doc(app, scr)
    zones = [z for s in doc["slots"] if s["kind"] == "play" for z in s["layout"]["zones"]]
    assert zones and all(z["id"] == "main" for z in zones)
    wins = [w for z in zones for w in z.get("overrides", [])]
    assert wins and all(w["override_id"] == r.json()["id"] for w in wins)  # `bandeau` ignoré


# --- forçage : cible layout, zone, validations ------------------------------------
async def test_override_layout_screen_and_playback(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    pl = await mk_playlist(admin, p, "Prog")
    await schedule(admin, p, pl, "screen", scr, "S")
    lay = await mk_layout(admin, p, "Urgence", zone("main", playlist_id=pl))
    r = await ov(
        admin, p, target_type="screen", target_id=scr, layout_id=lay["id"], duration_minutes=20
    )
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["layout_id"] == lay["id"] and o["playlist_id"] is None and o["zone_ids"] is None
    cur = (await playback(admin, p))[scr]
    assert cur["source"] == "override" and cur["layout_name"] == "Urgence"
    assert cur["playlist_name"] is None
    doc = await build_doc(app, scr)
    forced = [s for s in doc["slots"] if s["kind"] == "play" and s["source"]["kind"] == "override"]
    assert forced and forced[0]["source"]["layout_id"] == lay["id"]
    assert forced[0]["layout"]["id"] == lay["id"]
    in_use = await admin.delete(f"{p}/api/v1/layouts/{lay['id']}", headers=SAME)
    assert in_use.status_code == 409 and in_use.json()["params"]["overrides"] == [o["id"]]
    listed = (await admin.get(f"{p}/api/v1/overrides?scope=screen")).json()
    assert [x["id"] for x in listed] == [o["id"]]
    assert (await admin.get(f"{p}/api/v1/overrides?scope=zone")).json() == []


async def test_override_validation_codes(admin: AsyncClient, p: str) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    pl = await mk_playlist(admin, p)
    lay = await mk_layout(admin, p)
    tgt = {"target_type": "screen", "target_id": scr, "duration_minutes": 10}

    async def code(**body: Any) -> tuple[int, str | None]:
        r = await ov(admin, p, **tgt, **body)
        return r.status_code, r.json().get("code")

    assert await code(playlist_id=pl, layout_id=lay["id"]) == (422, "override.content_ambiguous")
    assert await code() == (422, "override.content_ambiguous")
    assert await code(layout_id=GHOST) == (422, "override.layout_not_found")
    assert await code(layout_id=lay["id"], zone_ids=["main"]) == (
        422,
        "override.zone_requires_playlist",
    )
    content = {"content_type": "content.image", "props": {}}
    assert await code(zone_ids=["main"], content=content) == (
        422,
        "override.zone_requires_playlist",
    )
    assert await code(kind="off", zone_ids=["main"]) == (422, "override.zone_not_content")
    assert await code(playlist_id=pl, zone_ids=["main", "main"]) == (
        422,
        "override.zone_ids_invalid",
    )
    too_many = [f"z{i}" for i in range(13)]
    assert await code(playlist_id=pl, zone_ids=too_many) == (422, "override.zone_ids_invalid")
    assert (await code(playlist_id=pl, zone_ids=["Bad Key"]))[0] == 422
    assert (await code(playlist_id=pl, zone_ids=[]))[0] == 422
    assert (await code(playlist_id=pl, zone_ids=["main"]))[0] == 201


async def test_override_preview_zone_coverage_and_candidates(admin: AsyncClient, p: str) -> None:
    site = await site_of(admin, p, "a")
    with_banner = await screen_of(admin, p, site, "Avec")
    without = await screen_of(admin, p, site, "Sans")
    pl = await mk_playlist(admin, p)
    a = await mk_layout(admin, p, "A", zone("main"), zone("bandeau", y=0.9, h=0.1, name="Bandeau"))
    b = await mk_layout(admin, p, "B", zone("main"))
    for scr, lay in ((with_banner, a), (without, b)):
        assert (await sched_layout(admin, p, lay["id"], scr)).status_code == 201
    body = {
        "target_type": "site",
        "target_id": site,
        "playlist_id": pl,
        "duration_minutes": 30,
        "zone_ids": ["bandeau", "main"],
    }
    r = await admin.post(f"{p}/api/v1/overrides/preview", json=body, headers=SAME)
    assert r.status_code == 200, r.text
    out = r.json()
    cov = {c["zone_id"]: c for c in out["zone_coverage"]}
    assert cov["bandeau"] == {"zone_id": "bandeau", "screens_total": 2, "screens_missing": 1}
    assert cov["main"]["screens_missing"] == 0
    cands = {c["key"]: c for c in out["zone_candidates"]}
    assert cands["main"]["layouts"] == 2
    assert cands["bandeau"] == {"key": "bandeau", "name": "Bandeau", "layouts": 1}
    assert out["superseded_override_ids"] == []
    # sans zone_ids : candidats seulement (sélecteur de l'admin)
    plain = {k: v for k, v in body.items() if k != "zone_ids"}
    out2 = (await admin.post(f"{p}/api/v1/overrides/preview", json=plain, headers=SAME)).json()
    assert out2["zone_coverage"] == []
    assert {c["key"] for c in out2["zone_candidates"]} == {"main", "bandeau"}
    created = await ov(admin, p, **body)
    assert created.json()["warnings"] == ["override_zone_missing"]
    assert created.json()["screens_missing"] == 1
    listed = (await admin.get(f"{p}/api/v1/overrides?scope=zone")).json()
    assert listed[0]["zone_ids"] == ["bandeau", "main"]
    usage = (await admin.get(f"{p}/api/v1/layouts/{a['id']}/usage")).json()
    assert [o["id"] for o in usage["zone_overrides"]] == [created.json()["id"]]


async def test_events_carry_zone_ids_and_audit(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    pl = await mk_playlist(admin, p)
    seen: list[Event] = []

    async def on(e: Event) -> None:
        seen.append(e)

    for name in ("override.created", "override.stopped", "layout.created"):
        app.state.runtime.bus.subscribe(name, on)
    lay = await mk_layout(admin, p)
    r = await ov(
        admin, p, target_type="screen", target_id=scr, playlist_id=pl,
        zone_ids=["main"], duration_minutes=10,
    )  # fmt: skip
    plain = await ov(
        admin, p, target_type="screen", target_id=scr, playlist_id=pl, duration_minutes=10
    )
    await admin.post(f"{p}/api/v1/overrides/{r.json()['id']}/stop", headers=SAME)
    by = {(e.name, e.payload.get("override_id")): e for e in seen}
    assert by[("override.created", r.json()["id"])].payload["zone_ids"] == ["main"]
    assert by[("override.created", plain.json()["id"])].payload["zone_ids"] is None
    assert by[("override.stopped", r.json()["id"])].payload["zone_ids"] == ["main"]
    assert any(e.name == "layout.created" and e.payload["layout_id"] == lay["id"] for e in seen)
    actions = await audit_actions(migrated_db_url)
    assert "layout.created" in actions and "override.create" in actions


# --- aperçu ------------------------------------------------------------------------------
async def test_preview_document(admin: AsyncClient, p: str) -> None:
    pl = await mk_playlist(admin, p)
    lay = await mk_layout(
        admin, p, "Aperçu", zone("main", playlist_id=pl), zone("coin", w=0.2, h=0.2, z=4)
    )
    r = await admin.get(f"{p}/api/v1/layouts/{lay['id']}/preview")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["format"] == 1 and doc["screen"]["orientation"] == "landscape"
    (slot,) = doc["slots"]
    assert slot["kind"] == "play" and [z["id"] for z in slot["layout"]["zones"]] == ["main", "coin"]
    span = datetime.fromisoformat(slot["end"]) - datetime.fromisoformat(slot["start"])
    assert span == timedelta(hours=1) and doc["revision"].startswith("sha256:")
    assert (await admin.get(f"{p}/api/v1/layouts/{GHOST}/preview")).status_code == 404
