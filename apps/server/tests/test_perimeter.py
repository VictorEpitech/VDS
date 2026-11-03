"""Périmètre (lot J, J1-J3) : migration 0018, filtres, 404/403, invariant J-D5, classement."""
# ruff: noqa: F811

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.routing import Mount
from test_auth import PASSWORD, SAME, admin, anon, app, base, clean, new_client, p  # noqa: F401
from test_diffusion import _content_types  # noqa: F401
from vds_server.core.perimeter import UNRESTRICTED, Node, covers, load_perimeter
from vds_server.core.scope_routes import MODULE_PREFIX, SCOPE_ROUTES


async def sql(url: str, stmt: str, **params: Any) -> list[Any]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        res = await c.execute(text(stmt), params)
        rows = list(res.all()) if res.returns_rows else []
    await engine.dispose()
    return rows


# --- migration 0018 ----------------------------------------------------------
def test_0018_editors_get_organization_and_downgrade(
    migrated_db_url: str, alembic_cfg: Config
) -> None:
    def run(stmt: str) -> list[Any]:
        return asyncio.run(sql(migrated_db_url, stmt))

    command.downgrade(alembic_cfg, "0017_mises_en_page")
    run("TRUNCATE user_account, organization, media CASCADE")
    run("INSERT INTO organization (name) VALUES ('Org')")
    run(
        "INSERT INTO user_account (auth_provider, external_id, display_name, role) VALUES "
        "('t', 'a', 'Admin', 'admin'), ('t', 'e', 'Ed', 'editor')"
    )
    run("INSERT INTO media (kind, title, mime) VALUES ('image', 'm', 'x')")
    command.upgrade(alembic_cfg, "heads")
    rows = run(
        "SELECT u.role, s.scope_type, u.role_source FROM user_account u "
        "LEFT JOIN user_scope s ON s.user_id = u.id ORDER BY u.role"
    )
    assert [tuple(r) for r in rows] == [("admin", None, "vds"), ("editor", "organization", "vds")]
    assert tuple(run("SELECT owner_site_id, owner_group_id FROM media")[0]) == (None, None)
    command.downgrade(alembic_cfg, "0017_mises_en_page")
    gone = run("SELECT to_regclass('user_scope'), count(*) FROM media")
    assert gone[0][0] is None and gone[0][1] == 1
    command.upgrade(alembic_cfg, "heads")
    run("TRUNCATE user_account, organization, media CASCADE")


# --- jeu de données ----------------------------------------------------------
async def mk_editor(
    app: FastAPI, admin: AsyncClient, p: str, url: str, name: str, nodes: list[tuple[str, str]]
) -> AsyncClient:
    email = f"{name}@vds.test"
    r = await admin.post(
        f"{p}/api/v1/m/auth.local/users",
        json={"email": email, "display_name": name, "password": PASSWORD, "role": "editor"},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    cols = {"organization": "organization_id", "site": "site_id", "group": "group_id"}
    for kind, node_id in nodes:
        await sql(
            url,
            f"INSERT INTO user_scope (user_id, {cols[kind]}) "  # noqa: S608 - colonne fixe
            "SELECT id, :n FROM user_account WHERE email = :e",
            n=UUID(node_id),
            e=email,
        )
    c = new_client(app)
    body = {"email": email, "password": PASSWORD}
    assert (
        await c.post(f"{p}/api/v1/m/auth.local/login", json=body, headers=SAME)
    ).status_code == 200
    r = await c.post(
        f"{p}/api/v1/m/auth.local/password",
        json={"current": PASSWORD, "new": PASSWORD + "-new"},
        headers=SAME,
    )
    assert r.status_code == 204, r.text
    return c


class World:
    ids: dict[str, str]
    clients: dict[str, AsyncClient]


async def org_id(admin: AsyncClient, p: str) -> str:
    return str((await admin.get(f"{p}/api/v1/organization")).json()["id"])


@pytest.fixture
async def world(
    app: FastAPI, admin: AsyncClient, p: str, migrated_db_url: str
) -> AsyncIterator[World]:
    w = World()
    ids: dict[str, str] = {}
    for slug in ("A", "B"):
        body = {"name": f"Site{slug}", "slug": slug.lower()}
        ids[slug] = (await admin.post(f"{p}/api/v1/sites", json=body, headers=SAME)).json()["id"]
    for key, site in (("A1", "A"), ("A2", "A"), ("B1", "B")):
        r = await admin.post(
            f"{p}/api/v1/sites/{ids[site]}/groups", json={"name": f"G{key}"}, headers=SAME
        )
        ids[key] = r.json()["id"]

    async def pl(name: str, owner: tuple[str, str] | None) -> str:
        body: dict[str, Any] = {"name": name}
        body["owner"] = None if owner is None else {"type": owner[0], "id": ids[owner[1]]}
        r = await admin.post(f"{p}/api/v1/playlists", json=body, headers=SAME)
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    ids["pS"] = await pl("PL-Shared", None)
    ids["pA"] = await pl("PL-A", ("site", "A"))
    ids["pA1"] = await pl("PL-A1", ("group", "A1"))
    ids["pA2"] = await pl("PL-A2", ("group", "A2"))
    ids["pB"] = await pl("PL-B", ("site", "B"))
    for key, title, site, group in (
        ("mA", "M-A", "A", None),
        ("mA2", "M-A2", "A", "A2"),
        ("mB", "M-B", "B", None),
    ):
        rows = await sql(
            migrated_db_url,
            "INSERT INTO media (kind, title, mime, status, owner_site_id, owner_group_id) "
            "VALUES ('image', :t, 'image/png', 'ready', :s, :g) RETURNING id",
            t=title,
            s=UUID(ids[site]),
            g=None if group is None else UUID(ids[group]),
        )
        ids[key] = str(rows[0][0])
    w.ids = ids
    url = migrated_db_url
    w.clients = {
        "ed_org": await mk_editor(
            app, admin, p, url, "ed_org", [("organization", await org_id(admin, p))]
        ),
        "ed_A": await mk_editor(app, admin, p, url, "ed_A", [("site", ids["A"])]),
        "ed_A1": await mk_editor(app, admin, p, url, "ed_A1", [("group", ids["A1"])]),
        "ed_none": await mk_editor(app, admin, p, url, "ed_none", []),
        "ed_AB": await mk_editor(
            app, admin, p, url, "ed_AB", [("site", ids["A"]), ("site", ids["B"])]
        ),
    }
    yield w
    for c in w.clients.values():
        await c.aclose()


def names(r: Any) -> set[str]:
    return {i["name"] for i in r.json()["items"]}


async def patch(c: AsyncClient, url: str, **body: Any) -> Any:
    return await c.patch(url, json=body, headers=SAME)


# --- lecture / écriture ------------------------------------------------------
async def test_read_filters_and_404(world: World, p: str) -> None:
    ids, cl = world.ids, world.clients
    url = f"{p}/api/v1/playlists"
    assert names(await cl["ed_org"].get(url)) == {"PL-Shared", "PL-A", "PL-A1", "PL-A2", "PL-B"}
    assert names(await cl["ed_A"].get(url)) == {"PL-Shared", "PL-A", "PL-A1", "PL-A2"}
    assert names(await cl["ed_A1"].get(url)) == {"PL-Shared", "PL-A", "PL-A1"}
    assert names(await cl["ed_none"].get(url)) == set()
    assert (await cl["ed_A"].get(f"{url}/{ids['pB']}")).status_code == 404
    assert (await cl["ed_A1"].get(f"{url}/{ids['pA2']}")).status_code == 404
    assert (await cl["ed_A1"].get(f"{url}/{ids['pA']}")).status_code == 200
    assert (await cl["ed_none"].get(f"{url}/{ids['pS']}")).status_code == 404
    media = await cl["ed_A1"].get(f"{p}/api/v1/media")
    assert {m["title"] for m in media.json()["items"]} == {"M-A"}
    assert (await cl["ed_A"].get(f"{p}/api/v1/media/{ids['mB']}")).status_code == 404
    assert "M-B" not in (await cl["ed_A"].get(f"{p}/api/v1/media")).text
    assert (await cl["ed_A"].get(f"{p}/api/v1/media/{ids['mB']}/usage")).status_code == 404


async def test_write_403_visible_not_modifiable(world: World, p: str) -> None:
    ids, cl = world.ids, world.clients
    url = f"{p}/api/v1/playlists"
    for key in ("pS", "pA"):  # partagé / ancêtre : visibles mais non modifiables en groupe
        r = await patch(cl["ed_A1"], f"{url}/{ids[key]}", name="x")
        assert (r.status_code, r.json()["code"]) == (403, "scope.forbidden"), key
    assert (await patch(cl["ed_A"], f"{url}/{ids['pS']}", name="x")).status_code == 403
    assert (await patch(cl["ed_A1"], f"{url}/{ids['pA1']}", name="ok")).status_code == 200
    assert (await patch(cl["ed_A"], f"{url}/{ids['pA2']}", name="ok2")).status_code == 200
    assert (await patch(cl["ed_A"], f"{url}/{ids['pB']}", name="x")).status_code == 404
    assert (await cl["ed_A1"].delete(f"{url}/{ids['pS']}", headers=SAME)).status_code == 403
    assert (await patch(cl["ed_org"], f"{url}/{ids['pS']}", name="S2")).status_code == 200


async def test_create_owner_rules(world: World, p: str) -> None:
    ids, cl = world.ids, world.clients
    url = f"{p}/api/v1/playlists"

    async def post(who: str, **body: Any) -> Any:
        return await cl[who].post(url, json={"name": f"n-{who}-{len(body)}", **body}, headers=SAME)

    r = await post("ed_A")
    assert r.status_code == 201 and r.json()["owner"] == {"type": "site", "id": ids["A"]}
    assert (await post("ed_A1")).json()["owner"] == {"type": "group", "id": ids["A1"]}
    r = await post("ed_A", owner=None)
    assert (r.status_code, r.json()["code"]) == (403, "scope.shared_forbidden")
    r = await post("ed_A1", owner={"type": "site", "id": ids["A"]})
    assert (r.status_code, r.json()["code"]) == (403, "scope.forbidden")
    assert (await post("ed_A", owner={"type": "site", "id": ids["B"]})).status_code == 404
    r = await post("ed_AB")
    assert (r.status_code, r.json()["code"]) == (422, "scope.owner_required")
    assert (await post("ed_none")).status_code == 403
    r = await post("ed_org")
    assert r.status_code == 201 and r.json()["owner"] is None
    r = await patch(cl["ed_A"], f"{url}/{ids['pA']}", owner=None)
    assert (r.status_code, r.json()["code"]) == (403, "scope.shared_forbidden")


async def test_reference_invariant(world: World, admin: AsyncClient, p: str) -> None:
    ids = world.ids
    url = f"{p}/api/v1/playlists/{ids['pA1']}/items"

    def item(media: str) -> dict[str, Any]:
        return {"content_type": "content.image", "props": {"src": media}}

    r = await admin.post(url, json=item(ids["mA2"]), headers=SAME)  # média A2 dans playlist A1
    assert (r.status_code, r.json()["code"]) == (422, "scope.reference_out_of_scope")
    assert (await admin.post(url, json=item(ids["mA"]), headers=SAME)).status_code == 201
    r = await world.clients["ed_A1"].post(url, json=item(ids["mB"]), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (422, "playlist.media_not_found")
    sched = {"name": "s", "playlist_id": ids["pA1"], "target_type": "site", "target_id": ids["A"]}
    r = await admin.post(f"{p}/api/v1/schedules", json=sched, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (422, "scope.reference_out_of_scope")
    ok = {**sched, "target_type": "group", "target_id": ids["A1"]}
    assert (await admin.post(f"{p}/api/v1/schedules", json=ok, headers=SAME)).status_code == 201
    r = await patch(
        admin, f"{p}/api/v1/media/{ids['mA']}", owner={"type": "group", "id": ids["A2"]}
    )
    assert (r.status_code, r.json()["code"]) == (409, "scope.owner_narrower_than_references")


async def test_schedules_screens_planning_fleet(world: World, admin: AsyncClient, p: str) -> None:
    ids, cl = world.ids, world.clients
    scr = {}
    for key, site, group in (("sA1", "A", "A1"), ("sA2", "A", "A2"), ("sB", "B", None)):
        body = {"site_id": ids[site], "name": f"Scr-{key}"}
        if group:
            body["group_id"] = ids[group]
        scr[key] = (await admin.post(f"{p}/api/v1/screens", json=body, headers=SAME)).json()["id"]

    def sch(name: str, pl: str, tt: str, tid: str) -> dict[str, Any]:
        return {"name": name, "playlist_id": pl, "target_type": tt, "target_id": tid}

    mk = {
        "org": sch("S-org", ids["pS"], "organization", await org_id(admin, p)),
        "A": sch("S-A", ids["pA"], "site", ids["A"]),
        "A1": sch("S-A1", ids["pA1"], "group", ids["A1"]),
        "B": sch("S-B", ids["pB"], "site", ids["B"]),
    }
    sid = {}
    for k, b in mk.items():
        r = await admin.post(f"{p}/api/v1/schedules", json=b, headers=SAME)
        assert r.status_code == 201, r.text
        sid[k] = r.json()["id"]
    ed = cl["ed_A1"]
    got = {s["name"] for s in (await ed.get(f"{p}/api/v1/schedules")).json()}
    assert got == {"S-org", "S-A", "S-A1"}  # règles d'ancêtres lisibles, S-B absente
    assert (await ed.get(f"{p}/api/v1/schedules/{sid['B']}")).status_code == 404
    r = await patch(ed, f"{p}/api/v1/schedules/{sid['A']}", enabled=False)
    assert (r.status_code, r.json()["code"]) == (403, "scope.forbidden")
    assert (await patch(ed, f"{p}/api/v1/schedules/{sid['A1']}", enabled=False)).status_code == 200
    # écrans, sites, groupes
    assert {s["name"] for s in (await ed.get(f"{p}/api/v1/screens")).json()} == {"Scr-sA1"}
    both = (await cl["ed_A"].get(f"{p}/api/v1/screens")).json()
    assert {s["name"] for s in both} == {"Scr-sA1", "Scr-sA2"}
    assert (await cl["ed_A"].get(f"{p}/api/v1/screens/{scr['sB']}")).status_code == 404
    assert (await ed.get(f"{p}/api/v1/screens/{scr['sA2']}")).status_code == 404
    assert {s["name"] for s in (await ed.get(f"{p}/api/v1/sites")).json()} == {"SiteA"}
    assert (await ed.get(f"{p}/api/v1/sites/{ids['B']}")).status_code == 404
    groups = (await ed.get(f"{p}/api/v1/sites/{ids['A']}/groups")).json()
    assert {g["name"] for g in groups} == {"GA1"}
    assert (await cl["ed_none"].get(f"{p}/api/v1/screens")).json() == []
    # planning : cible hors périmètre = 404 ; ETag distinct selon le périmètre
    q = {"target_type": "site", "target_id": ids["A"], "from": "2030-01-07", "to": "2030-01-08"}
    t_a = await cl["ed_A"].get(f"{p}/api/v1/planning/timeline", params=q)
    t_a1 = await ed.get(f"{p}/api/v1/planning/timeline", params=q)
    assert t_a.status_code == 200 and t_a1.headers["etag"] != t_a.headers["etag"]
    assert "Scr-sB" not in t_a.text and "PL-B" not in t_a.text and "S-B" not in t_a.text
    qb = {**q, "target_id": ids["B"]}
    assert (await cl["ed_A"].get(f"{p}/api/v1/planning/timeline", params=qb)).status_code == 404
    # fleet, config
    assert (await ed.get(f"{p}/api/v1/fleet/summary")).json()["screens"]["total"] == 1
    assert (await ed.get(f"{p}/api/v1/fleet/summary?site_id={ids['B']}")).status_code == 404
    assert (await cl["ed_A"].get(f"{p}/api/v1/config/screen/{scr['sB']}/core")).status_code == 404
    assert (await cl["ed_A"].get(f"{p}/api/v1/config/screen/{scr['sA1']}/core")).status_code == 200
    # forçage de contenu existant sur une cible du périmètre
    ov = {"kind": "playlist", "playlist_id": ids["pA1"], "target_type": "group",
          "target_id": ids["A1"], "duration_minutes": 30}  # fmt: skip
    r = await ed.post(f"{p}/api/v1/overrides", json=ov, headers=SAME)
    assert r.status_code == 201, r.text
    ov_id = r.json()["id"]
    assert (await cl["ed_A"].get(f"{p}/api/v1/overrides/{ov_id}")).status_code == 200
    assert (await cl["ed_none"].get(f"{p}/api/v1/overrides/{ov_id}")).status_code == 404
    bad = {**ov, "target_type": "site", "target_id": ids["B"]}
    assert (
        await cl["ed_A"].post(f"{p}/api/v1/overrides", json=bad, headers=SAME)
    ).status_code == 422


# --- unités ------------------------------------------------------------------
async def test_load_perimeter_and_covers(world: World, migrated_db_url: str) -> None:
    ids = world.ids
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)

    async def uid(s: AsyncSession, name: str) -> UUID:
        q = text("SELECT id FROM user_account WHERE email = :e")
        return UUID(str((await s.execute(q, {"e": f"{name}@vds.test"})).scalar_one()))

    async with AsyncSession(engine) as s:
        per = await load_perimeter(s, await uid(s, "ed_A1"), "editor")
        assert per.groups == {UUID(ids["A1"])} and per.sites_closure == {UUID(ids["A"])}
        assert per.groups_closure == {UUID(ids["A1"])} and not per.unrestricted
        per = await load_perimeter(s, await uid(s, "ed_A"), "editor")
        assert per.groups_closure == {UUID(ids["A1"]), UUID(ids["A2"])}
        assert (await load_perimeter(s, await uid(s, "ed_A"), "admin")) == UNRESTRICTED
        assert (await load_perimeter(s, await uid(s, "ed_none"), "editor")).empty
        assert (await load_perimeter(s, await uid(s, "ed_org"), "editor")).unrestricted
        a = Node("site", UUID(ids["A"]))
        a1 = Node("group", UUID(ids["A1"]))
        b = Node("site", UUID(ids["B"]))
        assert await covers(s, None, a1) and await covers(s, a, a1) and await covers(s, a, a)
        assert not await covers(s, a1, a) and not await covers(s, b, a1)
        assert not await covers(s, a, None)
    await engine.dispose()


# --- garde de classement -----------------------------------------------------
async def test_scope_routes_cover_every_api_route(app: FastAPI) -> None:
    inner = next(r.app for r in app.routes if isinstance(r, Mount) and isinstance(r.app, FastAPI))
    seen: set[str] = set()
    for path, ops in inner.openapi()["paths"].items():
        if not path.startswith("/api/v1") or path.startswith(MODULE_PREFIX):
            continue
        seen |= {f"{method.upper()} {path}" for method in ops}
    assert seen - set(SCOPE_ROUTES) == set(), "route /api/v1 non classée dans SCOPE_ROUTES"
    assert set(SCOPE_ROUTES) - seen == set(), "SCOPE_ROUTES cite une route inexistante"
