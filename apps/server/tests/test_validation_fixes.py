"""Correctifs de la revue sécurité « validation de publication » (forçage, dépendances, médias)."""
# ruff: noqa: F811

from pathlib import Path
from typing import Any
from uuid import UUID

from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import (  # noqa: F401
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
from test_layouts_api import mk_layout, zone
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401
from test_perimeter import World, world  # noqa: F401
from test_player_schedule import mk_item  # noqa: F401
from test_publication import Live, live, post, pub_url, sql, state  # noqa: F401
from test_storage_replace import _clean_tombstones, _png  # noqa: F401


async def set_review(admin: AsyncClient, p: str, value: bool) -> None:
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    r = await admin.put(
        f"{p}/api/v1/config/organization/{org}/core/diffusion.review_required",
        json={"value": value},
        headers=SAME,
    )
    assert r.status_code in (200, 204), r.text


def content_override(screen: str, media: UUID) -> dict[str, Any]:
    return {
        "target_type": "screen",
        "target_id": screen,
        "kind": "playlist",
        "duration_minutes": 30,
        "content": {"content_type": "content.image", "props": {"src": str(media)}},
    }


# --- 1 : forçage de contenu réservé à l'admin sous validation ------------------------------
async def test_content_override_requires_admin_under_review(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    media = await make_media(migrated_db_url)
    body = content_override(live.screen, media)
    r = await editor.post(f"{p}/api/v1/overrides", json=body, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (403, "override.content_requires_admin")
    assert await live.items() == 0
    assert (await admin.post(f"{p}/api/v1/overrides", json=body, headers=SAME)).status_code == 201
    await set_review(admin, p, False)  # validation désactivée : l'éditeur retrouve le droit
    ok = await editor.post(f"{p}/api/v1/overrides", json=body, headers=SAME)
    assert ok.status_code == 201, ok.text


# --- 1 bis : la playlist éphémère ne se modifie pas via /playlists ------------------------------
async def test_ephemeral_playlist_not_reachable_via_playlists(
    admin: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    media = await make_media(migrated_db_url)
    r = await admin.post(
        f"{p}/api/v1/overrides", json=content_override(live.screen, media), headers=SAME
    )
    assert r.status_code == 201, r.text
    eph = r.json()["playlist_id"]
    assert eph
    url = f"{p}/api/v1/playlists/{eph}"
    assert (await admin.get(url)).status_code == 404
    item = {"content_type": "content.image", "props": {"src": str(media)}, "duration_ms": 5000}
    add = await admin.post(f"{url}/items", json=item, headers=SAME)
    assert (add.status_code, add.json()["code"]) == (404, "playlist.not_found")
    assert (await admin.patch(url, json={"name": "x"}, headers=SAME)).status_code == 404
    assert (await admin.delete(url, headers=SAME)).status_code == 404
    rows = await sql(
        migrated_db_url, "SELECT count(*) FROM playlist_item WHERE playlist_id = :i", i=UUID(eph)
    )
    assert rows[0][0] == 1  # la playlist éphémère est intacte (l'élément du forçage seulement)


# --- 2 : approve + include_dependencies n'approuve que les dépendances soumises ------------------
async def test_include_dependencies_only_approves_in_review(
    admin: AsyncClient, editor: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl = await mk_playlist(editor, p, "Brouillon")
    sched = await admin.post(
        f"{p}/api/v1/schedules",
        json={"name": "S", "playlist_id": pl, "target_type": "screen", "target_id": live.screen},
        headers=SAME,
    )
    sid = sched.json()["id"]
    await post(admin, pub_url(p, "schedule", sid, "/submit"))
    rev = (await state(admin, p, "schedule", sid))["working_rev"]
    url = pub_url(p, "schedule", sid, "/approve")
    # dépendance en brouillon : jamais publiée d'office
    blocked = await post(admin, url, expected_rev=rev, include_dependencies=True)
    assert (blocked.status_code, blocked.json()["code"]) == (
        409,
        "publication.dependency_unpublished",
    )
    assert (await state(admin, p, "playlist", pl))["state"] == "draft"
    # soumise : approuvée avec la programmation, et auditée
    await post(editor, pub_url(p, "playlist", pl, "/submit"))
    ok = await post(admin, url, expected_rev=rev, include_dependencies=True)
    assert ok.status_code == 200 and ok.json()["state"] == "published"
    assert (await state(admin, p, "playlist", pl))["state"] == "published"
    rows = await sql(
        migrated_db_url,
        "SELECT count(*) FROM audit_log WHERE action = 'publication.approved' "
        "AND target_type = 'playlist' AND target_id = :t",
        t=pl,
    )
    assert rows[0][0] == 1


# --- 3 : suppression d'un média utilisé en fond par un instantané publié -----------------------
async def test_media_delete_refused_when_background_of_published_layout(
    content_modules: None, admin: AsyncClient, p: str, media_dir: Path
) -> None:
    mid = str((await upload(admin, p, _png("red")))[1]["id"])
    lay = await mk_layout(admin, p, "Fond", zone(), background_media_id=mid)
    r = await admin.delete(f"{p}/api/v1/media/{mid}", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "media.in_use")
    assert lay["id"] in r.json()["params"]["layouts"]


# --- 5 : la suppression d'une cible oublie la publication de ses programmations ------------------
async def test_target_delete_forgets_schedule_publications(
    admin: AsyncClient, live: Live, p: str, migrated_db_url: str
) -> None:
    pl = await mk_playlist(admin, p)
    r = await admin.post(
        f"{p}/api/v1/schedules",
        json={"name": "S", "playlist_id": pl, "target_type": "screen", "target_id": live.screen},
        headers=SAME,
    )
    sid = r.json()["id"]
    count = "SELECT count(*) FROM publication WHERE entity_type = 'schedule' AND entity_id = :s"
    assert (await sql(migrated_db_url, count, s=sid))[0][0] == 1
    assert (
        await admin.delete(f"{p}/api/v1/screens/{live.screen}", headers=SAME)
    ).status_code == 204
    assert (await sql(migrated_db_url, count, s=sid))[0][0] == 0


# --- 6 : playlist d'une zone d'une mise en page publiée ------------------------------------------
async def test_playlist_in_published_layout_zone_cannot_be_deleted(
    admin: AsyncClient, p: str
) -> None:
    pl = await mk_playlist(admin, p, "Zone")
    lay = await mk_layout(admin, p, "Hall", zone(playlist_id=pl))
    r = await admin.delete(f"{p}/api/v1/playlists/{pl}", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "playlist.in_use")
    assert lay["id"] in r.json()["params"]["layouts"]


# --- 7 : prolonger un média expiré présent dans un instantané ------------------------------------
async def test_extending_expired_published_media_requires_admin(
    content_modules: None,
    admin: AsyncClient,
    editor: AsyncClient,
    p: str,
    migrated_db_url: str,
    media_dir: Path,
) -> None:
    mid = str((await upload(admin, p, _png("red")))[1]["id"])
    pl = await mk_playlist(admin, p)
    item = {"content_type": "content.image", "props": {"src": mid}, "duration_ms": 5000}
    assert (
        await admin.post(f"{p}/api/v1/playlists/{pl}/items", json=item, headers=SAME)
    ).status_code == 201
    await sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() - interval '2 days', "
        "expired_at = now() - interval '1 day' WHERE id = :m",
        m=UUID(mid),
    )
    url = f"{p}/api/v1/media/{mid}"
    body = {"expires_at": "2100-01-01T00:00:00Z"}
    r = await editor.patch(url, json=body, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (403, "publication.media_in_use_requires_admin")
    bulk = await editor.post(
        f"{p}/api/v1/media/bulk",
        json={"ids": [mid], "action": "set_expiry", "expires_at": body["expires_at"]},
        headers=SAME,
    )
    assert bulk.json()["failed"] == [{"id": mid, "code": "publication.media_in_use_requires_admin"}]
    assert (await admin.patch(url, json=body, headers=SAME)).status_code == 200


# --- 9 : blocked_by ne révèle pas d'identifiants hors périmètre -----------------------------------
async def test_blocked_by_hides_out_of_scope_ids(
    world: World, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    ids, cl = world.ids, world.clients
    pa = UUID(ids["pA"])
    await sql(
        migrated_db_url,
        "UPDATE publication SET snapshot = NULL, state = 'draft' "
        "WHERE entity_type = 'playlist' AND entity_id = :i",
        i=pa,
    )  # playlist du site A jamais publiée
    r = await admin.post(
        f"{p}/api/v1/schedules",
        json={"name": "S", "playlist_id": ids["pA"], "target_type": "site", "target_id": ids["A"]},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    inside = await cl["ed_A"].get(pub_url(p, "schedule", sid))
    assert inside.json()["blocked_by"] == [{"entity_type": "playlist", "entity_id": ids["pA"]}]
    # la playlist change de site (hors du périmètre de ed_A) : son identifiant n'est plus exposé
    await sql(
        migrated_db_url,
        "UPDATE playlist SET owner_site_id = :b WHERE id = :i",
        b=UUID(ids["B"]),
        i=pa,
    )
    hidden = await cl["ed_A"].get(pub_url(p, "schedule", sid))
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["blocked_by"] == []
    assert (await state(admin, p, "schedule", sid))["blocked_by"] != []
