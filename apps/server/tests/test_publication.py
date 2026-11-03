"""Validation de publication (chantier B, vds-core 1.13.0), aux deux VDS_BASE_PATH.

Copie de travail éditable ; le résolveur ne lit que les instantanés publiés.
"""
# ruff: noqa: F811

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import (  # noqa: F401 - fixtures partagées
    _content_types,
    _diffusion_clean,
    make_media,
    mk_playlist,
    mk_screen,
)
from test_diffusion_media_files import (  # noqa: F401
    _clean_media,
    content_modules,
    media_dir,
    upload,
)
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401
from test_perimeter import World, world  # noqa: F401
from test_player_schedule import _entries, mk_item, mk_schedule  # noqa: F401
from test_storage_replace import _clean_tombstones, _png, replace  # noqa: F401
from vds_sdk import Event
from vds_server.publication.backfill import ensure_published_baseline


async def sql(url: str, query: str, **params: Any) -> list[Any]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        res = await c.execute(text(query), params)
        rows = [tuple(r) for r in res.all()] if res.returns_rows else []
    await engine.dispose()
    return rows


def pub_url(p: str, kind: str, oid: str, action: str = "") -> str:
    return f"{p}/api/v1/publications/{kind}/{oid}{action}"


async def post(c: AsyncClient, url: str, **body: Any) -> Any:
    return await c.post(url, json=body, headers=SAME)


async def state(c: AsyncClient, p: str, kind: str, oid: str) -> dict[str, Any]:
    r = await c.get(pub_url(p, kind, oid))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


class Live:
    """Un player appairé sur un écran : lit la programmation résolue."""

    def __init__(self, player: AsyncClient, p: str, token: str, screen: str) -> None:
        self.player, self.p, self.token, self.screen = player, p, token, screen

    async def doc(self) -> dict[str, Any]:
        r = await self.player.get(f"{self.p}/api/v1/player/schedule", headers=hdr(self.token))
        assert r.status_code == 200, r.text
        out: dict[str, Any] = r.json()
        return out

    async def items(self) -> int:
        return sum(len(v["items"]) for v in (await self.doc())["playlists"].values())


@pytest.fixture
async def live(admin: AsyncClient, app: FastAPI, p: str) -> Any:
    _, screen = await mk_screen(admin, p)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        yield Live(player, p, token, screen)


async def scheduled_playlist(admin: AsyncClient, live: Live, url: str) -> tuple[str, UUID]:
    """Playlist d'un élément, programmée en permanence sur l'écran (publiée par l'admin)."""
    media = await make_media(url)
    pl = await mk_playlist(admin, live.p)
    await mk_item(admin, live.p, pl, media)
    await mk_schedule(admin, live.p, pl, live.screen)
    return pl, media


# --- admin : auto-publication ---------------------------------------------------
async def test_admin_save_publishes_immediately(
    admin: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl, _ = await scheduled_playlist(admin, live, migrated_db_url)
    assert await live.items() == 1
    st = await state(admin, p, "playlist", pl)
    assert st["state"] == "published" and st["published_rev"] == st["working_rev"] >= 2
    got = (await admin.get(f"{p}/api/v1/playlists/{pl}")).json()
    assert got["publication"]["state"] == "published"
    listed = (await admin.get(f"{p}/api/v1/playlists")).json()["items"]
    assert listed[0]["publication"]["state"] == "published"
    sched = (await admin.get(f"{p}/api/v1/schedules")).json()
    assert sched[0]["publication"]["state"] == "published"


# --- V5 : l'éditeur ne change pas ce que jouent les écrans -------------------------
async def test_editor_change_is_invisible_until_approved(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl, _ = await scheduled_playlist(admin, live, migrated_db_url)
    before = await live.doc()
    m2 = await make_media(migrated_db_url, title="Deux")
    await mk_item(editor, p, pl, m2)
    working = (await editor.get(f"{p}/api/v1/playlists/{pl}")).json()
    assert len(working["items"]) == 2 and working["publication"]["state"] == "draft"
    assert working["publication"]["published_rev"] is not None  # modifications non publiées
    assert (await live.doc())["revision"] == before["revision"] and await live.items() == 1

    sub = await post(editor, pub_url(p, "playlist", pl, "/submit"), comment="ajout")
    assert sub.status_code == 200 and sub.json()["state"] == "in_review"
    assert (await live.doc())["revision"] == before["revision"]
    rev = sub.json()["working_rev"]

    stale = await post(admin, pub_url(p, "playlist", pl, "/approve"), expected_rev=rev - 1)
    assert (stale.status_code, stale.json()["code"]) == (409, "publication.stale")
    ok = await post(admin, pub_url(p, "playlist", pl, "/approve"), expected_rev=rev)
    assert ok.status_code == 200 and ok.json()["state"] == "published"
    assert await live.items() == 2 and (await live.doc())["revision"] != before["revision"]


async def test_reject_then_resubmit_and_withdraw(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl, _ = await scheduled_playlist(admin, live, migrated_db_url)
    await editor.patch(f"{p}/api/v1/playlists/{pl}", json={"name": "Neuf"}, headers=SAME)
    sub = await post(editor, pub_url(p, "playlist", pl, "/submit"))
    assert sub.json()["state"] == "in_review"
    again = await post(editor, pub_url(p, "playlist", pl, "/submit"))
    assert (again.status_code, again.json()["code"]) == (409, "publication.invalid_transition")
    wd = await post(editor, pub_url(p, "playlist", pl, "/withdraw"))
    assert wd.json()["state"] == "draft"
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    # l'éditeur ne peut ni approuver ni refuser
    for action, body in (("/approve", {"expected_rev": 1}), ("/reject", {"comment": "non"})):
        r = await post(editor, pub_url(p, "playlist", pl, action), **body)
        assert (r.status_code, r.json()["code"]) == (403, "auth.forbidden")
    empty = await post(admin, pub_url(p, "playlist", pl, "/reject"), comment="")
    assert empty.status_code == 422
    rej = await post(admin, pub_url(p, "playlist", pl, "/reject"), comment="Trop long")
    assert rej.json()["state"] == "draft" and rej.json()["review_comment"] == "Trop long"
    assert (await live.doc())["playlists"]  # inchangé
    # modifier la playlist après refus : resoumission nécessaire, puis validation
    await editor.patch(f"{p}/api/v1/playlists/{pl}", json={"name": "Neuf 2"}, headers=SAME)
    sub2 = await post(editor, pub_url(p, "playlist", pl, "/submit"), comment="corrigé")
    rev = sub2.json()["working_rev"]
    ok = await post(admin, pub_url(p, "playlist", pl, "/approve"), expected_rev=rev)
    assert ok.json()["state"] == "published" and ok.json()["review_comment"] is None
    # modifier un objet en revue le repasse en brouillon
    await editor.patch(f"{p}/api/v1/playlists/{pl}", json={"name": "N3"}, headers=SAME)
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    await editor.patch(f"{p}/api/v1/playlists/{pl}", json={"name": "N4"}, headers=SAME)
    assert (await state(editor, p, "playlist", pl))["state"] == "draft"
    inv = await post(admin, pub_url(p, "playlist", pl, "/approve"), expected_rev=1)
    assert (inv.status_code, inv.json()["code"]) == (409, "publication.invalid_transition")


async def test_list_filters_and_unknown_object(
    admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    pl = await mk_playlist(editor, p, "Brouillon")
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    queue = (await admin.get(f"{p}/api/v1/publications?state=in_review")).json()
    assert [(q["entity_id"], q["name"]) for q in queue] == [(pl, "Brouillon")]
    only = await admin.get(f"{p}/api/v1/publications?entity_type=schedule&state=in_review")
    assert only.json() == []
    assert (await admin.get(pub_url(p, "playlist", str(uuid4())))).status_code == 404
    assert (await admin.get(pub_url(p, "bogus", str(uuid4())))).status_code == 422


# --- dépendances ------------------------------------------------------------------
async def test_dependencies_and_blocked_publication(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str
) -> None:
    pl = await mk_playlist(editor, p, "Brouillon")  # jamais publiée
    r = await admin.post(
        f"{p}/api/v1/schedules",
        json={"name": "S", "playlist_id": pl, "target_type": "screen", "target_id": live.screen},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    pub = r.json()["publication"]  # l'enregistrement réussit mais reste en brouillon
    assert pub["state"] == "draft"
    assert pub["blocked_by"] == [{"entity_type": "playlist", "entity_id": pl}]
    sid = r.json()["id"]
    assert (await live.doc())["slots"][0]["kind"] == "off"
    # soumission puis approbation sans dépendances : 409 avec la liste ; avec : tout est publié
    await post(admin, pub_url(p, "schedule", sid, "/submit"))
    rev = (await state(admin, p, "schedule", sid))["working_rev"]
    blocked = await post(admin, pub_url(p, "schedule", sid, "/approve"), expected_rev=rev)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "publication.dependency_unpublished"
    assert blocked.json()["params"]["deps"] == [{"entity_type": "playlist", "entity_id": pl}]
    url = pub_url(p, "schedule", sid, "/approve")
    # la dépendance doit être soumise : un brouillon n'est jamais publié d'office
    still = await post(admin, url, expected_rev=rev, include_dependencies=True)
    assert still.status_code == 409 and still.json()["code"] == "publication.dependency_unpublished"
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    ok = await post(admin, url, expected_rev=rev, include_dependencies=True)
    assert ok.status_code == 200 and ok.json()["state"] == "published"
    assert (await state(admin, p, "playlist", pl))["state"] == "published"
    assert (await live.doc())["slots"][0]["kind"] == "play"


# --- suppression ------------------------------------------------------------------
async def test_delete_rules(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl, _ = await scheduled_playlist(admin, live, migrated_db_url)
    r = await editor.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (403, "publication.delete_requires_admin")
    sched = (await admin.get(f"{p}/api/v1/schedules")).json()[0]["id"]
    r = await editor.delete(f"{p}/api/v1/schedules/{sched}", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (403, "publication.delete_requires_admin")
    draft = await mk_playlist(editor, p, "Jamais publiée")
    assert (await editor.delete(f"{p}/api/v1/playlists/{draft}", headers=SAME)).status_code == 204
    q = "SELECT 1 FROM publication WHERE entity_id = :i"
    assert await sql(migrated_db_url, q, i=UUID(draft)) == []
    assert (await admin.delete(f"{p}/api/v1/schedules/{sched}", headers=SAME)).status_code == 204
    assert (await live.doc())["slots"][0]["kind"] == "off"
    assert (await admin.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)).status_code == 204


async def test_playlist_in_published_schedule_cannot_be_deleted(
    admin: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl, _ = await scheduled_playlist(admin, live, migrated_db_url)
    other = await mk_playlist(admin, p, "Autre")
    sched = (await admin.get(f"{p}/api/v1/schedules")).json()[0]["id"]
    # la copie de travail pointe ailleurs, l'instantané publié diffuse encore `pl`
    await sql(migrated_db_url, "UPDATE schedule SET playlist_id = :o", o=UUID(other))
    r = await admin.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "playlist.in_use")
    assert r.json()["params"]["schedules"] == [sched]


# --- V6 : forçage et médias -----------------------------------------------------------
def override_body(screen: str, playlist: str) -> dict[str, Any]:
    return {
        "target_type": "screen",
        "target_id": screen,
        "kind": "playlist",
        "playlist_id": playlist,
        "duration_minutes": 30,
    }


async def test_override_requires_published_content(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str
) -> None:
    pl = await mk_playlist(editor, p, "Jamais publiée")
    url = f"{p}/api/v1/overrides"
    r = await admin.post(url, json=override_body(live.screen, pl), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "publication.not_published")
    assert r.json()["params"]["entity_id"] == pl
    ok_pl = await mk_playlist(admin, p, "Publiée")
    r = await admin.post(url, json=override_body(live.screen, ok_pl), headers=SAME)
    assert r.status_code == 201, r.text


async def test_override_plays_published_version(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl = await mk_playlist(admin, p, "Urgence")
    await mk_item(admin, p, pl, await make_media(migrated_db_url))
    r = await admin.post(f"{p}/api/v1/overrides", json=override_body(live.screen, pl), headers=SAME)
    assert r.status_code == 201, r.text
    await mk_item(editor, p, pl, await make_media(migrated_db_url, title="2"))  # brouillon
    assert await live.items() == 1


async def test_override_with_inline_content_plays(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    media = await make_media(migrated_db_url)
    body = {
        "target_type": "screen",
        "target_id": live.screen,
        "kind": "playlist",
        "duration_minutes": 30,
        "content": {"content_type": "content.image", "props": {"src": str(media)}},
    }
    denied = await editor.post(f"{p}/api/v1/overrides", json=body, headers=SAME)
    assert (denied.status_code, denied.json()["code"]) == (403, "override.content_requires_admin")
    r = await admin.post(f"{p}/api/v1/overrides", json=body, headers=SAME)
    assert r.status_code == 201, r.text  # contenu inline : réservé admin sous validation
    assert await live.items() == 1


def media_item(media: str) -> dict[str, Any]:
    return {"content_type": "content.image", "props": {"src": media}, "duration_ms": 5000}


async def test_media_replace_requires_admin_when_published(
    content_modules: None,
    admin: AsyncClient,
    editor: AsyncClient,
    p: str,
    media_dir: Path,
) -> None:
    mid = str((await upload(admin, p, _png("red")))[1]["id"])
    assert (await replace(editor, p, mid, _png("blue")))[0] == 200  # non utilisé
    pl = await mk_playlist(admin, p)
    url = f"{p}/api/v1/playlists/{pl}/items"
    assert (await admin.post(url, json=media_item(mid), headers=SAME)).status_code == 201
    code, body = await replace(editor, p, mid, _png("green"))
    assert (code, body["code"]) == (403, "publication.media_in_use_requires_admin")
    assert (await replace(admin, p, mid, _png("green")))[0] == 200


async def count_media(url: str, mid: str) -> int:
    rows = await sql(url, "SELECT count(*) FROM media WHERE id = :m", m=UUID(mid))
    return int(rows[0][0])


async def test_purge_keeps_media_referenced_only_by_snapshot(
    content_modules: None,
    admin: AsyncClient,
    editor: AsyncClient,
    app: FastAPI,
    p: str,
    migrated_db_url: str,
    media_dir: Path,
) -> None:
    mid = str((await upload(admin, p, _png("red")))[1]["id"])
    pl = await mk_playlist(admin, p)
    base_url = f"{p}/api/v1/playlists/{pl}"
    r = await admin.post(f"{base_url}/items", json=media_item(mid), headers=SAME)
    item = r.json()["id"]
    assert (await editor.delete(f"{base_url}/items/{item}", headers=SAME)).status_code == 204
    assert (await admin.get(base_url)).json()["items"] == []  # copie de travail sans le média
    await sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() - interval '40 days', "
        "expired_at = now() - interval '31 days' WHERE id = :m",
        m=UUID(mid),
    )
    await app.state.runtime.content_sweeper.purge()
    assert await count_media(migrated_db_url, mid) == 1  # l'instantané le diffuse encore
    assert (await admin.get(f"{p}/api/v1/media/{mid}/usage")).status_code == 200
    r = await admin.delete(f"{p}/api/v1/media/{mid}", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "media.in_use")
    # l'admin republie la playlist (enregistrement) : plus aucune référence, purge possible
    await admin.patch(base_url, json={"name": "Ok"}, headers=SAME)
    await app.state.runtime.content_sweeper.purge()
    assert await count_media(migrated_db_url, mid) == 0


# --- périmètre ----------------------------------------------------------------------
async def test_scope_submit_and_visibility(world: World, p: str) -> None:
    ids, cl = world.ids, world.clients
    await cl["ed_A"].patch(f"{p}/api/v1/playlists/{ids['pA']}", json={"name": "A+"}, headers=SAME)
    ok = await post(cl["ed_A"], pub_url(p, "playlist", ids["pA"], "/submit"))
    assert ok.status_code == 200 and ok.json()["state"] == "in_review"
    # hors périmètre : 404, sans révéler l'existence
    r = await post(cl["ed_A"], pub_url(p, "playlist", ids["pB"], "/submit"))
    assert (r.status_code, r.json()["code"]) == (404, "playlist.not_found")
    assert (await cl["ed_A"].get(pub_url(p, "playlist", ids["pB"]))).status_code == 404
    none = await post(cl["ed_none"], pub_url(p, "playlist", ids["pA"], "/submit"))
    assert none.status_code == 404
    # visible mais non modifiable (ancêtre pour un éditeur de groupe) : 403
    r = await post(cl["ed_A1"], pub_url(p, "playlist", ids["pA"], "/submit"))
    assert (r.status_code, r.json()["code"]) == (403, "scope.forbidden")
    assert (await cl["ed_A1"].get(pub_url(p, "playlist", ids["pA"]))).status_code == 200
    queue = (await cl["ed_A"].get(f"{p}/api/v1/publications?state=in_review")).json()
    assert [q["entity_id"] for q in queue] == [ids["pA"]]
    assert (await cl["ed_AB"].get(f"{p}/api/v1/publications?state=in_review")).json() != []
    assert (await cl["ed_none"].get(f"{p}/api/v1/publications")).json() == []
    # un autre éditeur ne retire pas la soumission d'autrui
    other = await post(cl["ed_org"], pub_url(p, "playlist", ids["pA"], "/withdraw"))
    assert (other.status_code, other.json()["code"]) == (403, "auth.forbidden")


async def test_same_origin_required_on_posts(
    admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    pl = await mk_playlist(editor, p, "X")
    r = await editor.post(pub_url(p, "playlist", pl, "/submit"), json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "errors.origin_mismatch"
    r = await admin.post(pub_url(p, "playlist", pl, "/approve"), json={"expected_rev": 1})
    assert r.status_code == 403


# --- audit, événements, réglage ------------------------------------------------------
TRANSITIONS = [
    "publication.submitted",
    "publication.withdrawn",
    "publication.submitted",
    "publication.rejected",
    "publication.submitted",
    "publication.approved",
]


async def test_audit_and_events(
    admin: AsyncClient, editor: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    seen: list[Event] = []

    async def on(event: Event) -> None:
        seen.append(event)

    for name in ("submitted", "withdrawn", "approved", "rejected"):
        app.state.runtime.bus.subscribe(f"publication.{name}", on)
    pl = await mk_playlist(editor, p, "Evt")
    sub = await post(editor, pub_url(p, "playlist", pl, "/submit"), comment="svp")
    await post(editor, pub_url(p, "playlist", pl, "/withdraw"))
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    rev = (await state(admin, p, "playlist", pl))["working_rev"]
    await post(admin, pub_url(p, "playlist", pl, "/reject"), comment="non")
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    await post(admin, pub_url(p, "playlist", pl, "/approve"), expected_rev=rev)
    assert [e.name for e in seen] == TRANSITIONS
    first = seen[0].payload
    assert first["v"] == 1 and first["entity_type"] == "playlist" and first["comment"] == "svp"
    assert first["rev"] == sub.json()["working_rev"]
    approved = seen[-1].payload
    assert approved["auto"] is False and approved["author_id"] == first["actor_id"]
    rows = await sql(
        migrated_db_url,
        "SELECT action, target_type, details FROM audit_log WHERE action LIKE 'publication.%' "
        "ORDER BY id",
    )
    assert [r[0] for r in rows] == TRANSITIONS
    assert all(r[1] == "playlist" and "rev" in r[2] for r in rows)
    assert rows[0][2]["comment"] == "svp" and rows[3][2]["comment"] == "non"


async def test_review_required_setting(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    cfg = await admin.get(f"{p}/api/v1/config/organization/{org}/core")
    assert cfg.json()["values"]["diffusion.review_required"]["value"] is True
    pl, _ = await scheduled_playlist(admin, live, migrated_db_url)
    off = await admin.put(
        f"{p}/api/v1/config/organization/{org}/core/diffusion.review_required",
        json={"value": False},
        headers=SAME,
    )
    assert off.status_code in (200, 204), off.text
    await mk_item(editor, p, pl, await make_media(migrated_db_url, title="2"))
    assert (await state(editor, p, "playlist", pl))["state"] == "published"  # publié d'office
    assert await live.items() == 2
    # l'éditeur peut alors supprimer un objet publié
    other = await mk_playlist(admin, p)
    assert (await editor.delete(f"{p}/api/v1/playlists/{other}", headers=SAME)).status_code == 204


# --- reprise de l'existant ---------------------------------------------------------------
async def test_baseline_is_idempotent_and_makes_existing_objects_play(
    admin: AsyncClient, live: Live, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    media = await make_media(migrated_db_url)
    pl = await mk_playlist(admin, p, "Héritée")
    await mk_item(admin, p, pl, media)
    await sql(migrated_db_url, "DELETE FROM publication")  # état d'avant la migration 0021
    await sql(
        migrated_db_url,
        "INSERT INTO schedule (name, playlist_id, target_type, target_id) "
        "VALUES ('S', :pl, 'screen', :s)",
        pl=UUID(pl),
        s=UUID(live.screen),
    )
    assert await live.items() == 0  # sans instantané, rien n'est diffusé
    maker = app.state.runtime.maker
    assert await ensure_published_baseline(maker) >= 2
    assert await ensure_published_baseline(maker) == 0
    assert await live.items() == 1
    rows = await sql(migrated_db_url, "SELECT DISTINCT state FROM publication")
    assert rows == [("published",)]


async def test_baseline_removes_orphans(app: FastAPI, migrated_db_url: str) -> None:
    ghost = uuid4()
    await sql(
        migrated_db_url,
        "INSERT INTO publication (entity_type, entity_id, state, snapshot) "
        "VALUES ('playlist', :i, 'published', '{}')",
        i=ghost,
    )
    await ensure_published_baseline(app.state.runtime.maker)
    q = "SELECT 1 FROM publication WHERE entity_id = :i"
    assert await sql(migrated_db_url, q, i=ghost) == []


# --- migration 0021 --------------------------------------------------------------------------
def test_0021_constraints_and_reversible(migrated_db_url: str, alembic_cfg: Any) -> None:
    import asyncio

    from alembic import command
    from sqlalchemy.exc import DBAPIError

    def run(query: str, **params: Any) -> list[Any]:
        return asyncio.run(sql(migrated_db_url, query, **params))

    ghost = uuid4()
    ins = "INSERT INTO publication (entity_type, entity_id, state) VALUES (:t, :i, :s)"
    run(ins, t="playlist", i=ghost, s="draft")
    for t, s in (("media", "draft"), ("playlist", "archived")):
        with pytest.raises(DBAPIError):
            run(ins, t=t, i=uuid4(), s=s)
    with pytest.raises(DBAPIError):
        run(ins, t="playlist", i=ghost, s="draft")  # clé primaire (type, id)
    row = run("SELECT working_rev, snapshot FROM publication WHERE entity_id = :i", i=ghost)
    assert row == [(1, None)]
    idx = run("SELECT indexname FROM pg_indexes WHERE tablename = 'publication' ORDER BY 1")
    assert [r[0] for r in idx] == [
        "ix_publication_in_review",
        "ix_publication_schedule_target",
        "pk_publication",
    ]
    command.downgrade(alembic_cfg, "0020_flux_donnees")
    assert run("SELECT to_regclass('publication')") == [(None,)]
    command.upgrade(alembic_cfg, "heads")
    assert run("SELECT count(*) FROM publication") == [(0,)]


async def test_admin_save_approves_pending_revision(
    admin: AsyncClient, editor: AsyncClient, app: FastAPI, p: str
) -> None:
    seen: list[Event] = []

    async def on(event: Event) -> None:
        seen.append(event)

    app.state.runtime.bus.subscribe("publication.approved", on)
    pl = await mk_playlist(editor, p, "A valider")
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    r = await admin.patch(f"{p}/api/v1/playlists/{pl}", json={"name": "Vu"}, headers=SAME)
    assert r.status_code == 200 and r.json()["publication"]["state"] == "published"
    auto = seen[-1].payload
    assert auto["auto"] is True and auto["author_id"] != auto["actor_id"]
    assert (await admin.get(f"{p}/api/v1/publications?state=in_review")).json() == []
