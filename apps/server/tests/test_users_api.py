"""API de gestion des périmètres (lot J4) : /me, /scope-tree, /users, gardes, événements."""
# ruff: noqa: F811

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_auth import PASSWORD, SAME, admin, anon, app, base, clean, p  # noqa: F401
from test_perimeter import World, mk_editor, org_id, sql, world  # noqa: F401
from vds_sdk import Event
from vds_sdk.server import Identity
from vds_server.core import users as users_core
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.user_admin import UserAdminService, UserPatch


async def user_id_of(url: str, email: str) -> str:
    rows = await sql(url, "SELECT id FROM user_account WHERE email = :e", e=email)
    return str(rows[0][0])


async def put_scopes(
    c: AsyncClient, p: str, uid: str, scopes: list[dict[str, str]], etag: str
) -> Any:
    return await c.put(
        f"{p}/api/v1/users/{uid}/scopes",
        json={"scopes": scopes},
        headers={**SAME, "If-Match": etag},
    )


async def etag_of(c: AsyncClient, p: str, uid: str) -> str:
    r = await c.get(f"{p}/api/v1/users/{uid}")
    assert r.status_code == 200, r.text
    return str(r.headers["ETag"])


# --- /me et /scope-tree ---------------------------------------------------------------
async def test_me_perimeter(world: World, admin: AsyncClient, p: str) -> None:
    me = (await admin.get(f"{p}/api/v1/me")).json()
    assert me["role_source"] == "vds"
    assert me["perimeter"] == {"unrestricted": True, "can_share": True, "nodes": []}
    me = (await world.clients["ed_A"].get(f"{p}/api/v1/me")).json()
    assert me["perimeter"]["unrestricted"] is False and me["perimeter"]["can_share"] is False
    assert [(n["type"], n["id"], n["name"]) for n in me["perimeter"]["nodes"]] == [
        ("site", world.ids["A"], "SiteA")
    ]
    me = (await world.clients["ed_org"].get(f"{p}/api/v1/me")).json()
    assert me["perimeter"]["unrestricted"] is True and me["perimeter"]["can_share"] is True
    me = (await world.clients["ed_none"].get(f"{p}/api/v1/me")).json()
    assert me["perimeter"] == {"unrestricted": False, "can_share": False, "nodes": []}


async def test_scope_tree(world: World, admin: AsyncClient, p: str) -> None:
    ids, cl = world.ids, world.clients
    tree = (await admin.get(f"{p}/api/v1/scope-tree")).json()
    assert tree["can_share"] is True
    assert {s["name"] for s in tree["sites"]} == {"SiteA", "SiteB"}
    assert all(s["writable"] for s in tree["sites"])
    a = (await cl["ed_A"].get(f"{p}/api/v1/scope-tree")).json()
    assert a["can_share"] is False
    assert [s["id"] for s in a["sites"]] == [ids["A"]]
    assert a["sites"][0]["writable"] is True
    assert {g["id"]: g["writable"] for g in a["sites"][0]["groups"]} == {
        ids["A1"]: True,
        ids["A2"]: True,
    }
    # éditeur de groupe : le site parent est visible mais pas modifiable
    a1 = (await cl["ed_A1"].get(f"{p}/api/v1/scope-tree")).json()
    assert [(s["id"], s["writable"]) for s in a1["sites"]] == [(ids["A"], False)]
    assert [(g["id"], g["writable"]) for g in a1["sites"][0]["groups"]] == [(ids["A1"], True)]
    assert "SiteB" not in str(a1)
    assert (await cl["ed_none"].get(f"{p}/api/v1/scope-tree")).json()["sites"] == []


# --- /users ---------------------------------------------------------------------------
async def test_users_admin_only(world: World, p: str) -> None:
    ed = world.clients["ed_A"]
    assert (await ed.get(f"{p}/api/v1/users")).status_code == 403
    assert (await ed.patch(f"{p}/api/v1/users/{uuid4()}", json={}, headers=SAME)).status_code == 403
    r = await ed.put(
        f"{p}/api/v1/users/{uuid4()}/scopes",
        json={"scopes": []},
        headers={**SAME, "If-Match": '"x"'},
    )
    assert r.status_code == 403


async def test_users_list_filters(world: World, admin: AsyncClient, p: str) -> None:
    page = (await admin.get(f"{p}/api/v1/users")).json()
    assert page["total"] == 6  # admin + 5 éditeurs
    by_name = {u["display_name"]: u for u in page["items"]}
    assert by_name["ed_A"]["scopes"] == [{"type": "site", "id": world.ids["A"], "name": "SiteA"}]
    assert by_name["ed_none"]["scopes"] == []
    assert by_name["ed_A"]["role_source"] == "vds"
    eds = (await admin.get(f"{p}/api/v1/users", params={"role": "editor"})).json()
    assert eds["total"] == 5
    q = (await admin.get(f"{p}/api/v1/users", params={"q": "ed_A"})).json()
    assert {u["display_name"] for u in q["items"]} == {"ed_A", "ed_A1", "ed_AB"}
    assert (await admin.get(f"{p}/api/v1/users", params={"q": "%"})).json()["total"] == 0
    # site B : admin, ed_org, ed_AB
    b = (await admin.get(f"{p}/api/v1/users", params={"site_id": world.ids["B"]})).json()
    got = {u["display_name"] for u in b["items"]}
    assert {"ed_org", "ed_AB"} <= got
    assert not got & {"ed_A", "ed_A1", "ed_none"}
    a = (await admin.get(f"{p}/api/v1/users", params={"site_id": world.ids["A"]})).json()
    assert {"ed_A", "ed_A1", "ed_org", "ed_AB"} <= {u["display_name"] for u in a["items"]}
    assert "ed_none" not in {u["display_name"] for u in a["items"]}


# --- PUT scopes -----------------------------------------------------------------------
async def test_put_scopes_replaces_and_emits(
    world: World, admin: AsyncClient, p: str, app: FastAPI, migrated_db_url: str
) -> None:
    seen: list[Event] = []

    async def on_event(e: Event) -> None:
        seen.append(e)

    app.state.runtime.bus.subscribe("user.scope_changed", on_event, subscriber="test")
    ids = world.ids
    uid = await user_id_of(migrated_db_url, "ed_none@vds.test")
    etag = await etag_of(admin, p, uid)
    nodes = [{"type": "site", "id": ids["B"]}, {"type": "group", "id": ids["A1"]}]
    r = await put_scopes(admin, p, uid, nodes + nodes, etag)  # doublons ignorés
    assert r.status_code == 200, r.text
    got = {(n["type"], n["id"]) for n in r.json()["scopes"]}
    assert got == {("site", ids["B"]), ("group", ids["A1"])}
    assert r.headers["ETag"] != etag
    assert len(seen) == 1
    assert seen[0].payload["user_id"] == uid and seen[0].payload["version"] == 1
    assert len(seen[0].payload["scopes"]) == 2
    audit = await sql(
        migrated_db_url, "SELECT count(*) FROM audit_log WHERE action = 'user.scope_changed'"
    )
    assert audit[0][0] == 1
    # effet immédiat : l'éditeur voit B, plus rien d'autre
    me = (await world.clients["ed_none"].get(f"{p}/api/v1/me")).json()
    assert len(me["perimeter"]["nodes"]) == 2
    # remplacement par un ensemble vide
    r = await put_scopes(admin, p, uid, [], r.headers["ETag"])
    assert r.status_code == 200 and r.json()["scopes"] == []


async def test_put_scopes_if_match(
    world: World, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    uid = await user_id_of(migrated_db_url, "ed_none@vds.test")
    r = await admin.put(f"{p}/api/v1/users/{uid}/scopes", json={"scopes": []}, headers=SAME)
    assert r.status_code == 428 and r.json()["code"] == "user.if_match_required"
    r = await put_scopes(admin, p, uid, [], '"deadbeef"')
    assert r.status_code == 412 and r.json()["code"] == "user.scopes_conflict"
    etag = await etag_of(admin, p, uid)
    node = [{"type": "site", "id": world.ids["A"]}]
    assert (await put_scopes(admin, p, uid, node, etag)).status_code == 200
    # l'ancien ETag est périmé (deux administrateurs concurrents)
    r = await put_scopes(admin, p, uid, [], etag)
    assert r.status_code == 412
    # préfixe faible toléré
    fresh = await etag_of(admin, p, uid)
    assert (await put_scopes(admin, p, uid, [], f"W/{fresh}")).status_code == 200


async def test_put_scopes_limits_and_unknown(
    world: World, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    uid = await user_id_of(migrated_db_url, "ed_none@vds.test")
    etag = await etag_of(admin, p, uid)
    too_many = [{"type": "group", "id": str(uuid4())} for _ in range(101)]
    r = await put_scopes(admin, p, uid, too_many, etag)
    assert r.status_code == 422 and r.json()["code"] == "scope.too_many_nodes"
    r = await put_scopes(admin, p, uid, [{"type": "site", "id": str(uuid4())}], etag)
    assert r.status_code == 422 and r.json()["code"] == "scope.node_unknown"
    r = await put_scopes(admin, p, uid, [{"type": "organization", "id": str(uuid4())}], etag)
    assert r.status_code == 422 and r.json()["code"] == "scope.node_unknown"
    # 100 nœuds distincts valides : accepté (le plafond est inclusif)
    sites = [world.ids["A"], world.ids["B"]]
    ok = [{"type": "site", "id": i} for i in sites]
    assert (await put_scopes(admin, p, uid, ok, etag)).status_code == 200
    assert (
        await admin.put(
            f"{p}/api/v1/users/{uuid4()}/scopes",
            json={"scopes": []},
            headers={**SAME, "If-Match": '"x"'},
        )
    ).status_code == 404


async def test_put_scopes_exactly_100_nodes(
    world: World, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    ids = world.ids
    uid = await user_id_of(migrated_db_url, "ed_none@vds.test")
    for i in range(98):
        await sql(
            migrated_db_url,
            "INSERT INTO screen_group (site_id, name) VALUES (:s, :n)",
            s=UUID(ids["A"]),
            n=f"bulk{i}",
        )
    gids = [str(r[0]) for r in await sql(migrated_db_url, "SELECT id FROM screen_group")]
    assert len(gids) == 101
    etag = await etag_of(admin, p, uid)
    r = await put_scopes(admin, p, uid, [{"type": "group", "id": g} for g in gids[:100]], etag)
    assert r.status_code == 200 and len(r.json()["scopes"]) == 100
    etag = await etag_of(admin, p, uid)
    r = await put_scopes(admin, p, uid, [{"type": "group", "id": g} for g in gids], etag)
    assert r.status_code == 422 and r.json()["code"] == "scope.too_many_nodes"


# --- PATCH : rôle, activation, gardes ---------------------------------------------------
async def test_patch_role_and_events(
    world: World, admin: AsyncClient, p: str, app: FastAPI, migrated_db_url: str
) -> None:
    seen: list[Event] = []

    async def on_event(e: Event) -> None:
        seen.append(e)

    app.state.runtime.bus.subscribe("user.role_changed", on_event, subscriber="test")
    uid = await user_id_of(migrated_db_url, "ed_A@vds.test")
    r = await admin.patch(f"{p}/api/v1/users/{uid}", json={"role": "admin"}, headers=SAME)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    assert [e.payload for e in seen] == [
        {"version": 1, "user_id": uid, "from_role": "editor", "to_role": "admin", "source": "vds"}
    ]
    # le périmètre est conservé, effet immédiat sur /me (admin = non restreint)
    me = (await world.clients["ed_A"].get(f"{p}/api/v1/me")).json()
    assert me["role"] == "admin" and me["perimeter"]["unrestricted"] is True
    r = await admin.patch(f"{p}/api/v1/users/{uid}", json={"is_active": False}, headers=SAME)
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = await admin.patch(f"{p}/api/v1/users/{uid}", json={"nope": 1}, headers=SAME)
    assert r.status_code == 422
    assert (
        await admin.patch(f"{p}/api/v1/users/{uuid4()}", json={"role": "admin"}, headers=SAME)
    ).status_code == 404


async def test_patch_self_guards(admin: AsyncClient, p: str) -> None:
    me_id = (await admin.get(f"{p}/api/v1/me")).json()["id"]
    r = await admin.patch(f"{p}/api/v1/users/{me_id}", json={"role": "editor"}, headers=SAME)
    assert r.status_code == 422 and r.json()["code"] == "user.self_demotion"
    r = await admin.patch(f"{p}/api/v1/users/{me_id}", json={"is_active": False}, headers=SAME)
    assert r.status_code == 422 and r.json()["code"] == "user.self_deactivation"
    # inchangé : pas d'erreur (idempotent)
    r = await admin.patch(f"{p}/api/v1/users/{me_id}", json={"role": "admin"}, headers=SAME)
    assert r.status_code == 200


async def test_patch_last_admin(admin: AsyncClient, p: str, app: FastAPI) -> None:
    """Garde défensive : un acteur sans compte actif (course, tâche système) ne retire pas le
    dernier admin actif ; avec un second admin actif, c'est permis."""
    rt = app.state.runtime
    me_id = UUID((await admin.get(f"{p}/api/v1/me")).json()["id"])
    ghost = Principal(
        user_id=uuid4(), provider="x", email=None, display_name="g", role="admin", locale=None
    )
    for body in ({"role": "editor"}, {"is_active": False}):
        async with rt.maker() as s:
            with pytest.raises(ApiError) as exc:
                await UserAdminService(s, None, ghost).patch(me_id, UserPatch(**body))
        assert exc.value.code == "user.last_admin" and exc.value.status == 409
    r = await admin.post(
        f"{p}/api/v1/m/auth.local/users",
        json={"email": "a2@vds.test", "display_name": "A2", "password": PASSWORD, "role": "admin"},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    async with rt.maker() as s:
        out = await UserAdminService(s, None, ghost).patch(me_id, UserPatch(role="editor"))
    assert out.role == "editor"


# --- mode provider ----------------------------------------------------------------------
async def test_role_managed_by_provider(
    world: World, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    uid = await user_id_of(migrated_db_url, "ed_A@vds.test")
    await sql(
        migrated_db_url,
        "UPDATE user_account SET role_source = 'provider' WHERE id = :i",
        i=UUID(uid),
    )
    r = await admin.patch(f"{p}/api/v1/users/{uid}", json={"role": "admin"}, headers=SAME)
    assert r.status_code == 409 and r.json()["code"] == "user.role_managed_by_provider"
    me = (await world.clients["ed_A"].get(f"{p}/api/v1/me")).json()
    assert me["role_source"] == "provider" and me["role"] == "editor"
    got = (await admin.get(f"{p}/api/v1/users/{uid}")).json()
    assert got["role_source"] == "provider"
    # le périmètre et l'activation restent modifiables
    r = await admin.patch(f"{p}/api/v1/users/{uid}", json={"is_active": False}, headers=SAME)
    assert r.status_code == 200
    etag = await etag_of(admin, p, uid)
    assert (await put_scopes(admin, p, uid, [], etag)).status_code == 200


async def test_provider_role_change_event(world: World, app: FastAPI, migrated_db_url: str) -> None:
    """Le rôle fourni par le provider émet `user.role_changed` (source=provider) via upsert."""
    rt = app.state.runtime
    async with rt.maker() as s:
        ident = Identity(
            provider="auth.local", external_id="prov-1", display_name="P", role="editor"
        )
        await users_core.upsert_identity(s, ident)
        await s.commit()
    events: list[Event] = []
    async with rt.maker() as s:
        ident = Identity(
            provider="auth.local", external_id="prov-1", display_name="P", role="admin"
        )
        row, _ = await users_core.upsert_identity(s, ident, events=events)
        await s.commit()
    assert row.role == "admin" and row.role_source == "provider"
    assert [e.payload["source"] for e in events] == ["provider"]
    assert events[0].payload["from_role"] == "editor" and events[0].payload["to_role"] == "admin"


async def test_org_id_stable(world: World, admin: AsyncClient, p: str) -> None:
    oid = await org_id(admin, p)
    uid = None
    page = (await admin.get(f"{p}/api/v1/users", params={"q": "ed_org"})).json()
    uid = page["items"][0]["id"]
    assert page["items"][0]["scopes"][0]["id"] == oid
    assert page["items"][0]["scopes"][0]["type"] == "organization"
    assert uid
