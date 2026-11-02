"""Médiathèque (M2) : pages, filtres, tri, tags, expiration, usage, bulk, droits ; deux bases."""
# ruff: noqa: F811

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, _diffusion_clean, audit_actions, make_media, mk_playlist  # noqa: F401
from test_fleet_playback import schedule, screen_of, site_of


@pytest.fixture(autouse=True)
async def _clean_all(migrated_db_url: str) -> AsyncIterator[None]:
    async def truncate() -> None:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            await c.execute(
                text("TRUNCATE playback_overrides, schedule, playlist, media, audit_log CASCADE")
            )
        await engine.dispose()

    await truncate()
    yield
    await truncate()


def iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


async def sql(url: str, stmt: str, **params: Any) -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text(stmt), params)
    await engine.dispose()


async def listing(c: AsyncClient, p: str, query: str = "") -> dict[str, Any]:
    r = await c.get(f"{p}/api/v1/media?{query}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def patch(c: AsyncClient, p: str, media_id: Any, **body: Any) -> Any:
    return await c.patch(f"{p}/api/v1/media/{media_id}", json=body, headers=SAME)


def titles_of(page: dict[str, Any]) -> list[str]:
    return [m["title"] for m in page["items"]]


async def test_paging_sort_stable(editor: AsyncClient, p: str, migrated_db_url: str) -> None:
    ids = [await make_media(migrated_db_url, title="Meme") for _ in range(5)]
    await make_media(migrated_db_url, title="Alpha")
    seen: list[str] = []
    for offset in (0, 2, 4):
        page = await listing(editor, p, f"sort=title&limit=2&offset={offset}")
        assert page["total"] == 6
        seen += [m["id"] for m in page["items"]]
    assert len(seen) == len(set(seen)) == 6  # aucun doublon, aucun trou : départage par id
    assert seen[0] not in {str(i) for i in ids}  # « Alpha » d'abord
    again = await listing(editor, p, "sort=title&limit=6")
    assert [m["id"] for m in again["items"]] == seen
    assert (await editor.get(f"{p}/api/v1/media?sort=nope")).status_code == 422
    assert (await editor.get(f"{p}/api/v1/media?limit=201")).status_code == 422


async def test_q_is_escaped(editor: AsyncClient, p: str, migrated_db_url: str) -> None:
    await make_media(migrated_db_url, title="100% pur")
    await make_media(migrated_db_url, title="a_b")
    await make_media(migrated_db_url, title="axb")
    await make_media(migrated_db_url, title="back\\slash")
    assert titles_of(await listing(editor, p, "q=%25")) == ["100% pur"]
    assert titles_of(await listing(editor, p, "q=a_b")) == ["a_b"]
    assert titles_of(await listing(editor, p, "q=%5C")) == ["back\\slash"]
    await sql(
        migrated_db_url, "UPDATE media SET original_filename='Menu-Mai.PNG' WHERE title='axb'"
    )
    assert titles_of(await listing(editor, p, "q=menu-mai")) == ["axb"]


async def test_tags_rules_and_autocomplete(
    editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    a = await make_media(migrated_db_url, title="A")
    b = await make_media(migrated_db_url, title="B")
    r = await patch(editor, p, a, tags=["  Hall ", "hall", "Été", "promo_2026"])
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["hall", "promo_2026", "été"]
    await patch(editor, p, b, tags=["hall", "cafet"])
    assert sorted(titles_of(await listing(editor, p, "tags=hall"))) == ["A", "B"]
    assert titles_of(await listing(editor, p, "tags=hall,cafet")) == ["B"]
    assert titles_of(await listing(editor, p, "tags=hall&tags=été")) == ["A"]
    assert (await editor.get(f"{p}/api/v1/media/tags?prefix=H")).json() == ["hall"]
    assert (await editor.get(f"{p}/api/v1/media/tags?prefix=pro")).json() == ["promo_2026"]
    assert (await editor.get(f"{p}/api/v1/media/tags?prefix=_")).json() == []
    assert (await editor.get(f"{p}/api/v1/media/tags?prefix=%25")).json() == []
    assert len((await editor.get(f"{p}/api/v1/media/tags")).json()) == 4
    # bornes
    r = await patch(editor, p, a, tags=[f"t{i}" for i in range(21)])
    assert r.status_code == 422 and r.json()["code"] == "tags.too_many"
    ok = await patch(editor, p, a, tags=[f"t{i}" for i in range(20)])
    assert ok.status_code == 200 and len(ok.json()["tags"]) == 20
    for bad in ("-x", "x" * 33, "", "a,b", "a/b", "a\nb"):
        r = await patch(editor, p, a, tags=[bad])
        assert r.status_code == 422 and r.json()["code"] == "tags.invalid", bad
    assert (await editor.get(f"{p}/api/v1/media?tags=-x")).status_code == 422
    assert (await patch(editor, p, a)).status_code == 422  # corps vide


async def test_expiry_patch_states_and_restore(
    editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    soon = await make_media(migrated_db_url, title="Bientot")
    far = await make_media(migrated_db_url, title="Loin")
    gone = await make_media(migrated_db_url, title="Perime")
    never = await make_media(migrated_db_url, title="Jamais")
    assert (await patch(editor, p, soon, expires_at=iso(timedelta(days=2)))).status_code == 200
    await patch(editor, p, far, expires_at=iso(timedelta(days=60)))
    await patch(editor, p, gone, expires_at=iso(timedelta(hours=1)))
    await sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() - interval '2 hours', expired_at = now() "
        "WHERE id = :i",
        i=gone,
    )

    async def titles(state: str) -> list[str]:
        return sorted(titles_of(await listing(editor, p, f"state={state}")))

    assert await titles("all") == ["Bientot", "Jamais", "Loin", "Perime"]
    assert await titles("active") == ["Bientot", "Jamais", "Loin"]
    assert await titles("expiring") == ["Bientot"]  # 7 jours par défaut
    assert await titles("expired") == ["Perime"]
    got = (await editor.get(f"{p}/api/v1/media/{gone}")).json()
    assert got["expired"] is True and got["expires_at"]
    by_expiry = titles_of(await listing(editor, p, "sort=expires_at"))
    assert by_expiry[-1] == "Jamais"  # sans date en dernier

    # un instant échu est « expiré » avant même le passage du balayeur
    await sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() - interval '1 second' WHERE id = :i",
        i=never,
    )
    assert "Jamais" in await titles("expired")

    # prolonger = restaurer ; expires_at null = jamais
    r = await patch(editor, p, gone, expires_at=iso(timedelta(days=30)))
    assert r.status_code == 200 and r.json()["expired"] is False
    r = await patch(editor, p, never, expires_at=None)
    assert r.json()["expires_at"] is None and r.json()["expired"] is False
    actions = await audit_actions(migrated_db_url)
    assert "media.restored" in actions and actions.count("media.updated") >= 5
    # date sans fuseau refusée, jamais interprétée
    r = await patch(editor, p, gone, expires_at="2030-01-01T10:00:00")
    assert r.status_code == 422 and r.json()["code"] == "expiry.timezone_required"
    # titre : renommage conservé, null refusé
    assert (await patch(editor, p, gone, title="Neuf")).json()["title"] == "Neuf"
    assert (await patch(editor, p, gone, title=None)).status_code == 422


async def test_usage_and_unused(
    admin: AsyncClient, editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    used = await make_media(migrated_db_url, title="Utilise")
    await make_media(migrated_db_url, title="Seul")
    pl = await mk_playlist(editor, p, "Accueil")
    other = await mk_playlist(editor, p, "Vide")
    r = await editor.post(
        f"{p}/api/v1/playlists/{pl}/items",
        json={"content_type": "content.image", "props": {"src": str(used)}},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    site = await site_of(admin, p, "a")
    scr = await screen_of(admin, p, site, "Hall")
    sch = await schedule(editor, p, pl, "site", site, "Matin")
    ov = await editor.post(
        f"{p}/api/v1/overrides",
        json={"target_type": "screen", "target_id": scr, "playlist_id": pl, "duration_minutes": 30},
        headers=SAME,
    )
    assert ov.status_code == 201, ov.text

    assert titles_of(await listing(editor, p, "unused=true")) == ["Seul"]
    assert titles_of(await listing(editor, p, "unused=false")) == ["Utilise"]
    row = next(m for m in (await listing(editor, p))["items"] if m["title"] == "Utilise")
    assert row["usage_count"] == 1 and row["thumb"] is None and row["revision"] == 1

    u = (await editor.get(f"{p}/api/v1/media/{used}/usage")).json()
    assert [x["id"] for x in u["playlists"]] == [pl] and u["playlists"][0]["expired"] is False
    assert [(x["id"], x["target_name"], x["active"]) for x in u["schedules"]] == [(sch, "a", True)]
    assert [(x["id"], x["active"]) for x in u["overrides"]] == [(ov.json()["id"], True)]
    assert u["screens_now"] == 1
    pu = (await editor.get(f"{p}/api/v1/playlists/{pl}/usage")).json()
    assert len(pu["schedules"]) == 1 and len(pu["overrides"]) == 1
    empty = (await editor.get(f"{p}/api/v1/playlists/{other}/usage")).json()
    assert empty == {"schedules": [], "overrides": []}
    assert (await editor.get(f"{p}/api/v1/media/{UUID(int=1)}/usage")).status_code == 404

    # variante thumb -> clé relative
    await sql(
        migrated_db_url,
        "INSERT INTO media_variant (media_id, name, mime, bytes, storage_key) "
        "VALUES (:m, 'thumb', 'image/webp', 5, 'ab/x/thumb.webp')",
        m=used,
    )
    row = next(m for m in (await listing(editor, p))["items"] if m["title"] == "Utilise")
    assert row["thumb"] == f"media/{used}/thumb"
    one = (await editor.get(f"{p}/api/v1/media/{used}")).json()
    assert one["thumb"] == f"media/{used}/thumb" and one["usage_count"] == 1


async def test_bulk(editor: AsyncClient, p: str, migrated_db_url: str) -> None:
    ids = [str(await make_media(migrated_db_url, title=f"M{i}")) for i in range(3)]
    pl = await mk_playlist(editor, p)
    await editor.post(
        f"{p}/api/v1/playlists/{pl}/items",
        json={"content_type": "content.image", "props": {"src": ids[0]}},
        headers=SAME,
    )

    async def bulk(**body: Any) -> Any:
        return await editor.post(f"{p}/api/v1/media/bulk", json=body, headers=SAME)

    r = await bulk(ids=ids, action="add_tags", tags=["Hall", "promo"])
    assert r.status_code == 200 and r.json() == {"done": ids, "failed": []}
    assert all(m["tags"] == ["hall", "promo"] for m in (await listing(editor, p))["items"])
    await bulk(ids=ids[:1], action="remove_tags", tags=["hall"])
    assert len((await listing(editor, p, "tags=hall"))["items"]) == 2
    r = await bulk(ids=ids, action="set_expiry", expires_at=iso(timedelta(days=3)))
    assert len(r.json()["done"]) == 3
    r = await bulk(ids=ids, action="set_expiry", expires_at=None)
    assert len(r.json()["done"]) == 3
    assert all(m["expires_at"] is None for m in (await listing(editor, p))["items"])
    missing = str(UUID(int=7))
    r = await bulk(ids=[*ids, missing], action="delete")
    out = r.json()
    assert sorted(out["done"]) == sorted(ids[1:])
    assert {f["id"]: f["code"] for f in out["failed"]} == {
        ids[0]: "media.in_use",
        missing: "media.not_found",
    }
    assert [m["id"] for m in (await listing(editor, p))["items"]] == [ids[0]]
    # tags : dépassement par fusion = échec par élément
    await patch(editor, p, ids[0], tags=[f"t{i}" for i in range(20)])
    r = await bulk(ids=[ids[0]], action="add_tags", tags=["extra"])
    assert r.json()["failed"] == [{"id": ids[0], "code": "tags.too_many"}]

    # validations d'entrée
    too_many = [str(UUID(int=i + 1)) for i in range(201)]
    assert (await bulk(ids=too_many, action="delete")).status_code == 422
    assert (await bulk(ids=[], action="delete")).status_code == 422
    assert (await bulk(ids=ids, action="set_expiry")).status_code == 422
    assert (await bulk(ids=ids, action="add_tags", tags=[])).status_code == 422
    assert (await bulk(ids=ids, action="add_tags", tags=["-x"])).status_code == 422
    assert (await bulk(ids=ids, action="nuke")).status_code == 422
    exactly = await bulk(ids=[str(UUID(int=i + 100)) for i in range(200)], action="delete")
    assert exactly.status_code == 200 and len(exactly.json()["failed"]) == 200


async def test_cross_origin_and_roles(
    admin: AsyncClient, editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    m = await make_media(migrated_db_url)
    evil = {"Origin": "https://evil.test"}
    for method, url, body in (
        ("PATCH", f"/api/v1/media/{m}", {"title": "X"}),
        ("POST", "/api/v1/media/bulk", {"ids": [str(m)], "action": "delete"}),
        ("PATCH", f"/api/v1/playlists/{m}", {"name": "X"}),
    ):
        r = await editor.request(method, f"{p}{url}", json=body, headers=evil)
        assert r.status_code == 403, (method, url)
    assert (await listing(editor, p))["total"] == 1
    # l'éditeur peut poser l'expiration
    assert (await patch(editor, p, m, expires_at=iso(timedelta(days=1)))).status_code == 200
    # anonyme : 401
    async with new_client(admin._transport.app) as anonymous:  # type: ignore[attr-defined]
        for url in ("/api/v1/media", "/api/v1/media/tags", f"/api/v1/media/{m}/usage"):
            assert (await anonymous.get(f"{p}{url}")).status_code == 401
        r = await anonymous.post(f"{p}/api/v1/media/bulk", json={}, headers=SAME)
        assert r.status_code == 401


async def test_playlists_page_tags_expiry(
    admin: AsyncClient, editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    a = await mk_playlist(editor, p, "Alpha")
    b = await mk_playlist(editor, p, "Beta_x")
    await mk_playlist(editor, p, "Gamma")
    r = await editor.patch(
        f"{p}/api/v1/playlists/{a}",
        json={"tags": ["Promo", "promo", "hall"], "expires_at": iso(timedelta(days=2))},
        headers=SAME,
    )
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["hall", "promo"] and r.json()["expired"] is False

    async def page(query: str = "") -> dict[str, Any]:
        r = await editor.get(f"{p}/api/v1/playlists?{query}")
        assert r.status_code == 200, r.text
        return dict(r.json())

    def names(d: dict[str, Any]) -> list[str]:
        return [x["name"] for x in d["items"]]

    full = await page()
    assert full["total"] == 3 and names(full) == ["Alpha", "Beta_x", "Gamma"]
    assert names(await page("sort=-name&limit=1&offset=1")) == ["Beta_x"]
    assert names(await page("q=_x")) == ["Beta_x"]
    assert names(await page("q=%25")) == []
    assert names(await page("tags=promo")) == ["Alpha"]
    assert names(await page("state=expiring")) == ["Alpha"]
    assert (await editor.get(f"{p}/api/v1/playlists/tags?prefix=pr")).json() == ["promo"]
    assert (await editor.get(f"{p}/api/v1/playlists?limit=500")).status_code == 422
    r = await editor.patch(
        f"{p}/api/v1/playlists/{b}", json={"tags": [f"t{i}" for i in range(21)]}, headers=SAME
    )
    assert r.status_code == 422 and r.json()["code"] == "tags.too_many"

    # expirée : refus d'une programmation et d'un forçage ; restauration par prolongation
    await sql(
        migrated_db_url,
        "UPDATE playlist SET expires_at = now() - interval '1 hour', expired_at = now() "
        "WHERE id = :i",
        i=a,
    )
    site = await site_of(admin, p, "s")
    body = {"name": "S", "playlist_id": a, "target_type": "site", "target_id": site}
    r = await editor.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 422 and r.json()["code"] == "playlist.expired"
    r = await editor.post(
        f"{p}/api/v1/overrides",
        json={"target_type": "site", "target_id": site, "playlist_id": a, "duration_minutes": 5},
        headers=SAME,
    )
    assert r.status_code == 422 and r.json()["code"] == "playlist.expired"
    assert (await page("state=expired"))["items"][0]["expired"] is True
    r = await editor.patch(f"{p}/api/v1/playlists/{a}", json={"expires_at": None}, headers=SAME)
    assert r.json()["expired"] is False and r.json()["expires_at"] is None
    assert "playlist.restored" in await audit_actions(migrated_db_url)
    assert (await editor.post(f"{p}/api/v1/schedules", json=body, headers=SAME)).status_code == 201
    # le forçage de contenu isolé ne laisse pas de playlist éphémère dans la liste
    eph = await editor.post(
        f"{p}/api/v1/overrides",
        json={
            "target_type": "site",
            "target_id": site,
            "content": {"content_type": "content.pdf", "props": {}},
            "duration_minutes": 5,
        },
        headers=SAME,
    )
    assert eph.status_code == 201, eph.text
    assert (await page())["total"] == 3  # playlist éphémère exclue


async def test_add_expired_media_to_playlist(
    editor: AsyncClient, p: str, migrated_db_url: str
) -> None:
    ok = await make_media(migrated_db_url, title="Ok")
    old = await make_media(migrated_db_url, title="Vieux")
    pl = await mk_playlist(editor, p)
    url = f"{p}/api/v1/playlists/{pl}/items"
    first = await editor.post(
        url, json={"content_type": "content.image", "props": {"src": str(old)}}, headers=SAME
    )
    assert first.status_code == 201
    await sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() - interval '1 minute' WHERE id = :i",
        i=old,
    )  # échu, balayeur pas encore passé
    r = await editor.post(
        url, json={"content_type": "content.image", "props": {"src": str(old)}}, headers=SAME
    )
    assert r.status_code == 422 and r.json()["code"] == "media.expired"
    # l'élément déjà présent reste modifiable ; un média expiré ne peut pas y être (ré)introduit
    item = first.json()["id"]
    r = await editor.patch(
        f"{url}/{item}",
        json={"props": {"src": str(old), "fit": "cover"}, "duration_ms": 4000},
        headers=SAME,
    )
    assert r.status_code == 200, r.text
    r = await editor.patch(f"{url}/{item}", json={"props": {"src": str(ok)}}, headers=SAME)
    assert r.status_code == 200
    r = await editor.patch(f"{url}/{item}", json={"props": {"src": str(old)}}, headers=SAME)
    assert r.status_code == 422 and r.json()["code"] == "media.expired"


async def test_core_content_config_keys_with_provenance(admin: AsyncClient, p: str) -> None:
    api = f"{p}/api/v1/config"
    site = await site_of(admin, p, "cfg")
    v = (await admin.get(f"{api}/site/{site}/core")).json()["values"]
    for key, default in (
        ("content.expired_retention_days", 30),
        ("media.replaced_grace_hours", 48),
        ("media.expiring_soon_days", 7),
    ):
        assert v[key]["value"] == default and v[key]["origin"]["level"] == "default"
    # clés d'organisation seulement
    r = await admin.put(
        f"{api}/site/{site}/core/media.expiring_soon_days", json={"value": 3}, headers=SAME
    )
    assert r.status_code == 422
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    r = await admin.put(
        f"{api}/organization/{org}/core/media.expiring_soon_days",
        json={"value": 3},
        headers=SAME,
    )
    assert r.status_code == 204, r.text
    r = await admin.put(
        f"{api}/organization/{org}/core/content.expired_retention_days",
        json={"value": 366},
        headers=SAME,
    )
    assert r.status_code == 422
    v = (await admin.get(f"{api}/organization/{org}/core")).json()["values"]
    assert v["media.expiring_soon_days"]["origin"]["level"] == "organization"
