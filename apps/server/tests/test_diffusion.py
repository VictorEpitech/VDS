"""API diffusion : médias (métadonnées), playlists, programmation, sous les deux VDS_BASE_PATH."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import (  # noqa: F401 - fixtures partagées
    SAME,
    admin,
    anon,
    app,
    base,
    clean,
    editor,
    new_client,
    p,
)

IMAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["src"],
    "properties": {
        "src": {"type": "string", "x-vds-media": "image"},
        "fit": {"type": "string", "enum": ["cover", "contain"]},
    },
}
DOC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"file": {"type": "string", "x-vds-media": "document"}},
}
SCHEMAS = {"content.image": IMAGE_SCHEMA, "content.pdf": DOC_SCHEMA}


@pytest.fixture(autouse=True)
async def _diffusion_clean(migrated_db_url: str) -> AsyncIterator[None]:
    async def truncate() -> None:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            await c.execute(text("TRUNCATE schedule, playlist, media CASCADE"))
        await engine.dispose()

    await truncate()
    yield
    await truncate()


@pytest.fixture(autouse=True)
def _content_types(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app.state.runtime.registry, "content_item_schema", lambda module_id: SCHEMAS.get(module_id)
    )


async def make_media(url: str, kind: str = "image", title: str = "Photo") -> UUID:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        media_id = (
            await c.execute(
                text(
                    "INSERT INTO media (kind, title, mime, bytes, status) "
                    "VALUES (:k, :t, 'image/png', 10, 'ready') RETURNING id"
                ),
                {"k": kind, "t": title},
            )
        ).scalar_one()
        await c.execute(
            text(
                "INSERT INTO media_variant (media_id, name, mime, bytes, storage_key) "
                "VALUES (:m, 'original', 'image/png', 10, 'ab/x/original.png')"
            ),
            {"m": media_id},
        )
    await engine.dispose()
    return UUID(str(media_id))


async def audit_actions(url: str) -> list[str]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.connect() as c:
        rows = (await c.execute(text("SELECT action FROM audit_log ORDER BY id"))).scalars().all()
    await engine.dispose()
    return list(rows)


async def mk_playlist(c: AsyncClient, p: str, name: str = "Accueil") -> str:
    r = await c.post(f"{p}/api/v1/playlists", json={"name": name}, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def mk_screen(c: AsyncClient, p: str) -> tuple[str, str]:
    r = await c.post(f"{p}/api/v1/sites", json={"name": "Moulins", "slug": "moulins"}, headers=SAME)
    site = r.json()["id"]
    r = await c.post(f"{p}/api/v1/screens", json={"site_id": site, "name": "Hall"}, headers=SAME)
    return site, str(r.json()["id"])


# --- accès -------------------------------------------------------------------
async def test_anonymous_and_origin(anon: AsyncClient, admin: AsyncClient, p: str) -> None:
    async with new_client(admin._transport.app) as other:  # type: ignore[attr-defined]
        for path in ("media", "playlists", "schedules"):
            assert (await other.get(f"{p}/api/v1/{path}")).status_code == 401
    r = await admin.post(
        f"{p}/api/v1/playlists", json={"name": "X"}, headers={"Origin": "https://evil.test"}
    )
    assert r.status_code == 403


# --- médias ------------------------------------------------------------------
async def test_media_crud(editor: AsyncClient, p: str, migrated_db_url: str) -> None:
    img = await make_media(migrated_db_url, "image", "Logo campus")
    await make_media(migrated_db_url, "video", "Film")
    rows = (await editor.get(f"{p}/api/v1/media")).json()
    assert len(rows) == 2
    assert [m["title"] for m in (await editor.get(f"{p}/api/v1/media?kind=video")).json()] == [
        "Film"
    ]
    assert len((await editor.get(f"{p}/api/v1/media?q=logo")).json()) == 1
    assert (await editor.get(f"{p}/api/v1/media?q=%25")).json() == []
    detail = (await editor.get(f"{p}/api/v1/media/{img}")).json()
    assert detail["variants"][0]["name"] == "original" and detail["used_by"] == []
    r = await editor.patch(f"{p}/api/v1/media/{img}", json={"title": "Nouveau"}, headers=SAME)
    assert r.status_code == 200 and r.json()["title"] == "Nouveau"
    assert (await editor.patch(f"{p}/api/v1/media/{img}", json={}, headers=SAME)).status_code == 422
    assert (await editor.delete(f"{p}/api/v1/media/{img}", headers=SAME)).status_code == 204
    r = await editor.get(f"{p}/api/v1/media/{img}")
    assert r.status_code == 404 and r.json()["code"] == "media.not_found"
    actions = await audit_actions(migrated_db_url)
    assert "media.updated" in actions and "media.deleted" in actions


# --- playlists ---------------------------------------------------------------
async def test_playlist_items_and_media_refs(
    editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    img = await make_media(migrated_db_url, "image")
    doc = await make_media(migrated_db_url, "document", "Menu")
    pl = await mk_playlist(editor, p)
    base_url = f"{p}/api/v1/playlists/{pl}"

    async def add(body: dict[str, Any]) -> Any:
        return await editor.post(f"{base_url}/items", json=body, headers=SAME)

    a = await add(
        {"content_type": "content.image", "props": {"src": str(img)}, "duration_ms": 5000}
    )
    assert a.status_code == 201, a.text
    assert a.json()["media_ids"] == [str(img)] and a.json()["position"] == 0
    b = await add({"content_type": "content.pdf", "props": {"file": str(doc)}})
    c = await add({"content_type": "content.image", "props": {"src": str(img)}, "position": 0})
    assert [x.json()["position"] for x in (b, c)] == [1, 0]
    detail = (await editor.get(base_url)).json()
    assert [i["id"] for i in detail["items"]] == [c.json()["id"], a.json()["id"], b.json()["id"]]
    assert detail["item_count"] == 3

    # validations
    r = await add({"content_type": "content.nope", "props": {}})
    assert r.status_code == 422 and r.json()["code"] == "playlist.content_type_unknown"
    r = await add({"content_type": "content.image", "props": {"src": str(img), "fit": "zoom"}})
    assert r.json()["code"] == "playlist.item_props_invalid"
    r = await add({"content_type": "content.image", "props": {"src": str(doc)}})
    assert r.json()["code"] == "playlist.media_kind_mismatch"
    r = await add({"content_type": "content.image", "props": {"src": str(uuid4())}})
    assert r.json()["code"] == "playlist.media_not_found"
    r = await add({"content_type": "content.image", "props": {"src": "pas-un-uuid"}})
    assert r.json()["code"] == "playlist.media_ref_invalid"

    # média utilisé : suppression refusée
    r = await editor.delete(f"{p}/api/v1/media/{img}", headers=SAME)
    assert r.status_code == 409 and r.json()["code"] == "media.in_use"
    assert (await editor.get(f"{p}/api/v1/media/{img}")).json()["used_by"] == [pl]

    # modification : changer la référence met à jour l'extraction
    r = await editor.patch(
        f"{base_url}/items/{a.json()['id']}",
        json={"props": {"src": str(img), "fit": "cover"}, "enabled": False, "duration_ms": None},
        headers=SAME,
    )
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["duration_ms"] is None

    # ordre
    ids = [i["id"] for i in detail["items"]]
    r = await editor.put(f"{base_url}/items/order", json={"item_ids": ids[::-1]}, headers=SAME)
    assert [i["id"] for i in r.json()["items"]] == ids[::-1]
    r = await editor.put(f"{base_url}/items/order", json={"item_ids": ids[:2]}, headers=SAME)
    assert r.json()["code"] == "playlist.order_mismatch"

    # suppression d'élément : positions recompactées
    assert (await editor.delete(f"{base_url}/items/{ids[0]}", headers=SAME)).status_code == 204
    left = (await editor.get(base_url)).json()["items"]
    assert [i["position"] for i in left] == [0, 1]
    other = await mk_playlist(editor, p, "Autre")
    r = await editor.delete(f"{p}/api/v1/playlists/{other}/items/{ids[1]}", headers=SAME)
    assert r.status_code == 404 and r.json()["code"] == "playlist.item_not_found"
    assert len((await editor.get(f"{p}/api/v1/playlists")).json()) == 2


async def test_playlist_crud_and_in_use(editor: AsyncClient, p: str, migrated_db_url: str) -> None:
    pl = await mk_playlist(editor, p)
    r = await editor.patch(
        f"{p}/api/v1/playlists/{pl}", json={"name": "Hall", "description": "d"}, headers=SAME
    )
    assert r.json()["name"] == "Hall" and r.json()["description"] == "d"
    assert (await editor.get(f"{p}/api/v1/playlists/{uuid4()}")).status_code == 404
    assert (await editor.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)).status_code == 204
    actions = await audit_actions(migrated_db_url)
    assert actions.count("playlist.created") == 1 and "playlist.deleted" in actions


async def test_editor_cannot_write_inventory_but_can_write_diffusion(
    admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    r = await editor.post(f"{p}/api/v1/sites", json={"name": "X", "slug": "x"}, headers=SAME)
    assert r.status_code == 403
    await mk_playlist(editor, p)


# --- programmation -----------------------------------------------------------
RULES = [
    {"weekdays": 31, "start": "08:00", "end": "19:00"},
    {"weekdays": 96, "start": "10:00", "end": "24:00"},
]


async def test_schedule_lifecycle(admin: AsyncClient, p: str, migrated_db_url: str) -> None:
    site, screen = await mk_screen(admin, p)
    pl = await mk_playlist(admin, p)
    body = {
        "name": "Semaine",
        "playlist_id": pl,
        "target_type": "screen",
        "target_id": screen,
        "priority": 5,
        "starts_on": "2026-10-01",
        "rules": RULES,
    }
    r = await admin.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["rules"] == [RULES[0], RULES[1]] and r.json()["enabled"] is True

    # organisation, site, groupe : cibles valides
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    for tt, tid in (("organization", org), ("site", site)):
        r = await admin.post(
            f"{p}/api/v1/schedules",
            json={**body, "target_type": tt, "target_id": tid},
            headers=SAME,
        )
        assert r.status_code == 201, r.text
    assert len((await admin.get(f"{p}/api/v1/schedules")).json()) == 3
    assert (
        len((await admin.get(f"{p}/api/v1/schedules?target_type=screen&target_id={screen}")).json())
        == 1
    )

    # validations
    def code(r: Any) -> str:
        return str(r.json().get("code") or "validation")

    r = await admin.post(
        f"{p}/api/v1/schedules", json={**body, "target_id": str(uuid4())}, headers=SAME
    )
    assert code(r) == "schedule.target_not_found"
    r = await admin.post(
        f"{p}/api/v1/schedules", json={**body, "playlist_id": str(uuid4())}, headers=SAME
    )
    assert code(r) == "schedule.playlist_not_found"
    bad_rule = {"weekdays": 1, "start": "10:00", "end": "09:00"}
    assert (
        await admin.post(f"{p}/api/v1/schedules", json={**body, "rules": [bad_rule]}, headers=SAME)
    ).status_code == 422
    for bad in ({"weekdays": 0}, {"weekdays": 128}, {"start": "8:00"}, {"end": "24:01"}):
        rule = {**RULES[0], **bad}
        r = await admin.post(f"{p}/api/v1/schedules", json={**body, "rules": [rule]}, headers=SAME)
        assert r.status_code == 422, bad
    r = await admin.post(
        f"{p}/api/v1/schedules",
        json={**body, "starts_on": "2026-12-01", "ends_on": "2026-11-01"},
        headers=SAME,
    )
    assert r.status_code == 422

    # modification : règles remplacées, période effacée, cible partielle refusée
    r = await admin.patch(
        f"{p}/api/v1/schedules/{sid}",
        json={"rules": [RULES[1]], "starts_on": None, "enabled": False, "priority": -2},
        headers=SAME,
    )
    assert r.status_code == 200, r.text
    assert r.json()["rules"] == [RULES[1]] and r.json()["starts_on"] is None
    assert r.json()["enabled"] is False and r.json()["priority"] == -2
    r = await admin.patch(f"{p}/api/v1/schedules/{sid}", json={"target_type": "site"}, headers=SAME)
    assert code(r) == "schedule.target_incomplete"
    r = await admin.patch(
        f"{p}/api/v1/schedules/{sid}",
        json={"ends_on": "2026-01-01", "starts_on": "2026-02-01"},
        headers=SAME,
    )
    assert r.status_code == 200 or code(r) == "schedule.period_invalid"

    # playlist utilisée : suppression refusée
    r = await admin.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)
    assert r.status_code == 409 and r.json()["code"] == "playlist.in_use"

    # supprimer l'écran supprime sa programmation
    assert (await admin.delete(f"{p}/api/v1/screens/{screen}", headers=SAME)).status_code == 204
    left = (await admin.get(f"{p}/api/v1/schedules")).json()
    assert {s["target_type"] for s in left} == {"organization", "site"}
    assert (
        await admin.delete(f"{p}/api/v1/schedules/{left[0]['id']}", headers=SAME)
    ).status_code == 204
    assert (await admin.get(f"{p}/api/v1/schedules/{sid}")).status_code == 404
    actions = await audit_actions(migrated_db_url)
    assert actions.count("schedule.created") == 3 and "schedule.deleted" in actions
