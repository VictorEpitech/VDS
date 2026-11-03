"""Lot J5 : matrice de non-fuite (docs/architecture-droits.md §6), aux deux VDS_BASE_PATH.

`ed_A` (site A) et `ed_none` (aucun périmètre) ne reçoivent, dans AUCUNE réponse (listes, détails,
usages, planning, fleet, incidents, aperçus, erreurs), ni nom ni identifiant d'objet du site B.
"""
# ruff: noqa: F811

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, p  # noqa: F401
from test_diffusion import _content_types  # noqa: F401
from test_perimeter import World, sql, world  # noqa: F401
from vds_sdk.server import Identity
from vds_server.core import users as users_core

BNAMES = ("SiteB", "GB1", "PL-B", "M-B", "S-B", "Scr-sB", "ZZB")


class Leak(World):
    b: dict[str, str]  # identifiants B (écran, règle, forçage, layout, média partagé…)
    forbidden: list[str]


@pytest.fixture
async def leak(world: World, admin: AsyncClient, p: str, migrated_db_url: str) -> Leak:
    w = Leak()
    w.ids, w.clients = world.ids, world.clients
    ids = w.ids
    b: dict[str, str] = {}

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = await admin.post(f"{p}/api/v1/{path}", json=body, headers=SAME)
        assert r.status_code in (200, 201), r.text
        return dict(r.json())

    b["scr"] = (await post("screens", {"site_id": ids["B"], "name": "Scr-sB-ZZB"}))["id"]
    b["scrG"] = (
        await post("screens", {"site_id": ids["B"], "group_id": ids["B1"], "name": "Scr-ZZB-g"})
    )["id"]
    await post("screens", {"site_id": ids["A"], "group_id": ids["A1"], "name": "Scr-A1"})
    b["sched"] = (
        await post(
            "schedules",
            {
                "name": "S-B-ZZB",
                "playlist_id": ids["pB"],
                "target_type": "site",
                "target_id": ids["B"],
            },  # fmt: skip
        )
    )["id"]
    b["ov"] = (
        await post(
            "overrides",
            {
                "kind": "playlist",
                "playlist_id": ids["pB"],
                "target_type": "site",
                "target_id": ids["B"],
                "duration_minutes": 30,
            },  # fmt: skip
        )
    )["id"]
    zone = {"key": "main", "name": "Main", "x": 0, "y": 0, "w": 1, "h": 1}
    lay = {"name": "Lay-ZZB", "orientation": "landscape", "zones": [zone]}
    b["lay"] = (await post("layouts", {**lay, "owner": {"type": "site", "id": ids["B"]}}))["id"]
    await post("layouts", {**lay, "name": "Lay-A", "owner": {"type": "site", "id": ids["A"]}})
    # média partagé référencé par une playlist A et une playlist B (usage : compteur seul)
    rows = await sql(
        migrated_db_url,
        "INSERT INTO media (kind, title, mime, status) VALUES ('image', 'M-Shared', 'image/png',"
        " 'ready') RETURNING id",
    )
    b["mS"] = str(rows[0][0])
    item = {"content_type": "content.image", "props": {"src": b["mS"]}}
    for pl in ("pA", "pB"):
        r = await admin.post(f"{p}/api/v1/playlists/{ids[pl]}/items", json=item, headers=SAME)
        assert r.status_code == 201, r.text
    await sql(
        migrated_db_url,
        "INSERT INTO incident (kind, severity, subject_type, subject_id, site_id, opened_at)"
        " VALUES ('screen.offline', 'warning', 'screen', :s, :site, now()),"
        " ('agent.offline', 'critical', 'agent', :a, :site, now())",
        s=b["scr"],
        a=str(uuid4()),
        site=UUID(ids["B"]),
    )
    w.b = b
    w.forbidden = [*BNAMES, ids["B"], ids["B1"], ids["pB"], b["scr"], b["scrG"], b["sched"],
                   b["ov"], b["lay"], ids["mB"]]  # fmt: skip
    return w


def assert_clean(r: Any, forbidden: list[str], what: str) -> None:
    for token in forbidden:
        assert token not in r.text, f"fuite de {token!r} dans {what}"


def list_urls(p: str, w: Leak) -> list[str]:
    ids = w.ids
    pre = f"{p}/api/v1"
    return [
        f"{pre}/me", f"{pre}/scope-tree", f"{pre}/organization", f"{pre}/sites",
        f"{pre}/sites/{ids['A']}", f"{pre}/sites/{ids['A']}/groups",
        f"{pre}/screens", f"{pre}/playlists", f"{pre}/playlists/tags",
        f"{pre}/media", f"{pre}/media/tags", f"{pre}/layouts",
        f"{pre}/schedules", f"{pre}/overrides", f"{pre}/incidents", f"{pre}/fleet/summary",
        f"{pre}/fleet/playback", f"{pre}/onboarding", f"{pre}/power/exceptions",
        f"{pre}/power/plans", f"{pre}/playlists/{ids['pA']}/usage",
        f"{pre}/media/{w.b['mS']}/usage", f"{pre}/media?q=B", f"{pre}/media?q=ZZB",
        f"{pre}/playlists?q=ZZB", f"{pre}/layouts?q=ZZB", f"{pre}/screens?q=ZZB",
        f"{pre}/schedules?q=ZZB",
        f"{pre}/planning/timeline?target_type=site&target_id={ids['A']}"
        "&from=2030-01-07&to=2030-01-08",
        f"{pre}/planning/at?target_type=site&target_id={ids['A']}&at=2030-01-07T10:00:00Z",
    ]  # fmt: skip


def foreign_urls(p: str, w: Leak) -> list[str]:
    """Détails d'objets B : 404 attendu pour ed_A."""
    ids, b = w.ids, w.b
    pre = f"{p}/api/v1"
    return [
        f"{pre}/sites/{ids['B']}", f"{pre}/sites/{ids['B']}/groups", f"{pre}/groups/{ids['B1']}",
        f"{pre}/screens/{b['scr']}", f"{pre}/screens/{b['scrG']}",
        f"{pre}/playlists/{ids['pB']}", f"{pre}/playlists/{ids['pB']}/usage",
        f"{pre}/media/{ids['mB']}", f"{pre}/media/{ids['mB']}/usage",
        f"{pre}/layouts/{b['lay']}", f"{pre}/layouts/{b['lay']}/usage",
        f"{pre}/layouts/{b['lay']}/preview",
        f"{pre}/schedules/{b['sched']}", f"{pre}/overrides/{b['ov']}",
        f"{pre}/power/screens/{b['scr']}",
        f"{pre}/config/screen/{b['scr']}/core", f"{pre}/config/site/{ids['B']}/core",
        f"{pre}/fleet/summary?site_id={ids['B']}",
        f"{pre}/planning/timeline?target_type=site&target_id={ids['B']}"
        "&from=2030-01-07&to=2030-01-08",
        f"{pre}/planning/timeline?target_type=screen&target_id={b['scr']}"
        "&from=2030-01-07&to=2030-01-08",
    ]  # fmt: skip


@pytest.mark.parametrize("who", ["ed_A", "ed_none", "ed_A1"])
async def test_no_foreign_name_in_any_get(leak: Leak, p: str, who: str) -> None:
    c = leak.clients[who]
    for url in list_urls(p, leak):
        r = await c.get(url)
        assert r.status_code in (200, 404, 403), (url, r.status_code)
        assert_clean(r, leak.forbidden, f"GET {url} ({who})")
    for url in foreign_urls(p, leak):
        r = await c.get(url)
        assert r.status_code == 404, (who, url, r.status_code, r.text)  # 404, jamais 403
        assert_clean(r, leak.forbidden, f"GET {url} ({who})")


async def test_foreign_writes_are_404_without_leak(leak: Leak, p: str) -> None:
    c, ids, b = leak.clients["ed_A"], leak.ids, leak.b
    pre = f"{p}/api/v1"
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("PATCH", f"{pre}/playlists/{ids['pB']}", {"name": "x"}),
        ("DELETE", f"{pre}/playlists/{ids['pB']}", None),
        ("POST", f"{pre}/playlists/{ids['pB']}/items",
         {"content_type": "content.image", "props": {"src": ids["mB"]}}),
        ("PATCH", f"{pre}/media/{ids['mB']}", {"title": "x"}),
        ("DELETE", f"{pre}/media/{ids['mB']}", None),
        ("PUT", f"{pre}/layouts/{b['lay']}", {"name": "x", "orientation": "landscape",
                                              "zones": [{"key": "m", "name": "M", "x": 0, "y": 0,
                                                         "w": 1, "h": 1}]}),
        ("DELETE", f"{pre}/layouts/{b['lay']}", None),
        ("POST", f"{pre}/layouts/{b['lay']}/duplicate", {}),
        ("PATCH", f"{pre}/schedules/{b['sched']}", {"enabled": False}),
        ("DELETE", f"{pre}/schedules/{b['sched']}", None),
        ("PATCH", f"{pre}/overrides/{b['ov']}", {"duration_minutes": 5}),
        ("POST", f"{pre}/overrides/{b['ov']}/stop", {}),
        ("POST", f"{pre}/schedules", {"name": "x", "playlist_id": ids["pA"],
                                      "target_type": "site", "target_id": ids["B"]}),
        ("POST", f"{pre}/overrides/preview", {"kind": "playlist", "playlist_id": ids["pA"],
                                              "target_type": "site", "target_id": ids["B"],
                                              "duration_minutes": 10}),
        ("POST", f"{pre}/playlists", {"name": "x", "owner": {"type": "site", "id": ids["B"]}}),
    ]  # fmt: skip
    for method, url, body in calls:
        r = await c.request(method, url, json=body, headers={**SAME, "If-Match": '"x"'})
        assert r.status_code in (404, 422), (method, url, r.status_code, r.text)
        if r.status_code == 404:
            assert_clean(r, leak.forbidden, f"{method} {url}")
        else:  # 422 : le corps renvoyé ne doit pas non plus nommer un objet B
            assert_clean(r, [n for n in leak.forbidden if n not in (ids["B"], b["scr"])],
                         f"{method} {url}")  # fmt: skip
    # le serveur n'a rien modifié
    r = await leak.clients["ed_org"].get(f"{pre}/playlists/{ids['pB']}")
    assert r.status_code == 200 and r.json()["name"] == "PL-B"


async def test_planning_simulate_and_override_preview_do_not_leak(leak: Leak, p: str) -> None:
    c, ids = leak.clients["ed_A"], leak.ids
    pre = f"{p}/api/v1"
    prev = {"kind": "playlist", "playlist_id": ids["pA"], "target_type": "site",
            "target_id": ids["A"], "duration_minutes": 10}  # fmt: skip
    r = await c.post(f"{pre}/overrides/preview", json=prev, headers=SAME)
    assert r.status_code == 200, r.text
    assert_clean(r, leak.forbidden, "overrides/preview")
    assert r.json()["superseded_override_ids"] == []  # le forçage B n'est jamais cité
    sim = {"target_type": "site", "target_id": ids["A"], "at": "2030-01-07T10:00:00Z"}
    r = await c.post(f"{pre}/planning/simulate", json=sim, headers=SAME)
    assert r.status_code in (200, 422), r.text
    assert_clean(r, leak.forbidden, "planning/simulate")


async def test_usage_hidden_count_only(leak: Leak, admin: AsyncClient, p: str) -> None:
    ids, b = leak.ids, leak.b
    url = f"{p}/api/v1/media/{b['mS']}/usage"
    ed = (await leak.clients["ed_A"].get(url)).json()
    full = (await admin.get(url)).json()
    assert full["playlists"], full
    assert ed["hidden_count"] >= 1 and full["hidden_count"] == 0
    assert_clean(await leak.clients["ed_A"].get(url), leak.forbidden, "usage média partagé")
    # suppression d'un partagé référencé hors périmètre : refus (403 non modifiable) sans nom
    r = await leak.clients["ed_A"].delete(f"{p}/api/v1/media/{b['mS']}", headers=SAME)
    assert r.status_code == 403
    assert_clean(r, leak.forbidden, "DELETE média partagé")
    # contenu A référencé par un référent B-visible seulement par l'admin : 409 compteur seul
    r = await leak.clients["ed_org"].delete(f"{p}/api/v1/media/{b['mS']}", headers=SAME)
    assert r.status_code in (204, 409)
    ed_b = await leak.clients["ed_A"].get(f"{p}/api/v1/playlists/{ids['pB']}/usage")
    assert ed_b.status_code == 404


async def test_shared_content_rules(leak: Leak, admin: AsyncClient, p: str) -> None:
    cl, ids = leak.clients, leak.ids
    pre = f"{p}/api/v1/playlists"
    for who in ("ed_org", "admin"):
        c = admin if who == "admin" else cl[who]
        r = await c.post(pre, json={"name": f"sh-{who}", "owner": None}, headers=SAME)
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        assert (await c.patch(f"{pre}/{pid}", json={"name": "m"}, headers=SAME)).status_code == 200
        assert (await c.delete(f"{pre}/{pid}", headers=SAME)).status_code == 204
    for who in ("ed_A", "ed_A1"):
        r = await cl[who].post(pre, json={"name": "s", "owner": None}, headers=SAME)
        assert (r.status_code, r.json()["code"]) == (403, "scope.shared_forbidden")
        assert (await cl[who].get(f"{pre}/{ids['pS']}")).status_code == 200
        assert (await cl[who].patch(f"{pre}/{ids['pS']}", json={"name": "x"}, headers=SAME)
                ).status_code == 403  # fmt: skip
        assert (await cl[who].delete(f"{pre}/{ids['pS']}", headers=SAME)).status_code == 403
    r = await cl["ed_A"].patch(f"{pre}/{ids['pA']}", json={"owner": None}, headers=SAME)
    assert r.status_code == 403
    # ed_none : rien de visible, 404 partout, y compris le partagé
    assert (await cl["ed_none"].get(pre)).json()["items"] == []
    assert (await cl["ed_none"].get(f"{pre}/{ids['pS']}")).status_code == 404
    assert (await cl["ed_none"].get(f"{p}/api/v1/media/{leak.b['mS']}")).status_code == 404


async def test_admin_never_limited(leak: Leak, admin: AsyncClient, p: str) -> None:
    ids = leak.ids
    pre = f"{p}/api/v1"
    for url in foreign_urls(p, leak):
        r = await admin.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text)
    me = (await admin.get(f"{pre}/me")).json()
    assert me["perimeter"]["unrestricted"] is True and me["perimeter"]["can_share"] is True
    assert "SiteB" in (await admin.get(f"{pre}/sites")).text
    assert "PL-B" in (await admin.get(f"{pre}/playlists")).text
    assert (await admin.patch(f"{pre}/playlists/{ids['pB']}", json={"name": "PL-B"},
                              headers=SAME)).status_code == 200  # fmt: skip


async def test_new_editor_has_no_scope_local_and_provider(
    leak: Leak, admin: AsyncClient, app: Any, p: str, migrated_db_url: str
) -> None:
    c = leak.clients["ed_none"]
    me = (await c.get(f"{p}/api/v1/me")).json()
    assert me["perimeter"]["nodes"] == [] and me["perimeter"]["unrestricted"] is False
    tree = (await c.get(f"{p}/api/v1/scope-tree")).json()
    assert_clean(await c.get(f"{p}/api/v1/scope-tree"), leak.forbidden, "scope-tree")
    assert not tree.get("sites")
    # création via provider : aucun user_scope
    rt = app.state.runtime
    async with rt.maker() as s:
        ident = Identity(provider="auth.local", external_id="prov-new", display_name="Pn",
                         role="editor")  # fmt: skip
        row, _ = await users_core.upsert_identity(s, ident)
        await s.commit()
        uid = row.id
    rows = await sql(migrated_db_url, "SELECT count(*) FROM user_scope WHERE user_id = :u", u=uid)
    assert rows[0][0] == 0
    got = (await admin.get(f"{p}/api/v1/users/{uid}")).json()
    assert got["scopes"] == [] and got["role_source"] == "provider"


async def test_etag_differs_between_perimeters(leak: Leak, p: str) -> None:
    ids = leak.ids
    q = {"target_type": "site", "target_id": ids["A"], "from": "2030-01-07", "to": "2030-01-08"}
    tags = set()
    for who in ("ed_A", "ed_A1", "ed_org", "ed_AB"):
        r = await leak.clients[who].get(f"{p}/api/v1/planning/timeline", params=q)
        assert r.status_code == 200, (who, r.text)
        tags.add(r.headers["etag"])
    assert len(tags) >= 3  # ed_A / ed_A1 / (ed_org, ed_AB) : vues différentes, jamais confondues
