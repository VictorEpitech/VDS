from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from vds_server.core.runtime import Runtime
from vds_server.main import create_app
from vds_server.settings import Settings

REPO_MODULES = Path(__file__).resolve().parents[3] / "modules"
ORIGIN = "https://vds.test"
SAME = {"Origin": ORIGIN}
PASSWORD = "correct horse battery"  # noqa: S105 - mot de passe de test


@pytest.fixture(params=["/", "/espace/affichage"])
def base(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def p(base: str) -> str:
    return "" if base == "/" else base


async def _truncate(url: str) -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(
            text(
                "TRUNCATE m_auth_local_session, m_auth_local_credential,"
                " m_auth_local_login_attempt, audit_log,"
                " config_override, screen, screen_group, site, user_account,"
                " organization, event_outbox CASCADE"
            )
        )
    await engine.dispose()


@pytest.fixture
async def clean(migrated_db_url: str) -> AsyncIterator[None]:
    await _truncate(migrated_db_url)
    yield
    await _truncate(migrated_db_url)  # ne pas polluer les autres fichiers de tests


@pytest.fixture
async def app(base: str, migrated_db_url: str, clean: None) -> AsyncIterator[FastAPI]:
    settings = Settings(
        base_path=base,
        database_url=migrated_db_url,
        public_origin=ORIGIN,
        modules_dir=REPO_MODULES,
    )
    runtime = Runtime(settings)
    application = create_app(settings, runtime=runtime)
    async with runtime.lifespan(application):
        assert runtime.startup_ok()
        yield application


def new_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN)


@pytest.fixture
async def anon(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with new_client(app) as c:
        yield c


async def bootstrap(c: AsyncClient, p: str) -> None:
    r = await c.post(
        f"{p}/api/v1/m/auth.local/bootstrap",
        json={"email": "root@vds.test", "display_name": "Root", "password": PASSWORD},
        headers=SAME,
    )
    assert r.status_code == 201, r.text


@pytest.fixture
async def admin(anon: AsyncClient, p: str) -> AsyncClient:
    await bootstrap(anon, p)
    return anon


@pytest.fixture
async def editor(app: FastAPI, admin: AsyncClient, p: str) -> AsyncIterator[AsyncClient]:
    r = await admin.post(
        f"{p}/api/v1/m/auth.local/users",
        json={"email": "ed@vds.test", "display_name": "Ed", "password": PASSWORD, "role": "editor"},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    async with new_client(app) as c:
        r = await c.post(
            f"{p}/api/v1/m/auth.local/login",
            json={"email": "ed@vds.test", "password": PASSWORD},
            headers=SAME,
        )
        assert r.status_code == 200
        yield c


async def audit_actions(migrated_db_url: str) -> list[str]:
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.connect() as c:
        rows = await c.execute(text("SELECT action FROM audit_log ORDER BY id"))
        out = [r[0] for r in rows]
    await engine.dispose()
    return out


async def test_first_admin_login_logout(anon: AsyncClient, p: str) -> None:
    m = f"{p}/api/v1/m/auth.local"
    assert (await anon.get(f"{m}/status")).json() == {"initialized": False}
    assert (await anon.get(f"{p}/api/v1/me")).status_code == 401
    r = await anon.post(
        f"{m}/bootstrap",
        json={"email": "root@vds.test", "display_name": "Root", "password": "court"},
        headers=SAME,
    )
    assert r.status_code == 422 and r.json()["code"] == "auth_local.password_too_short"
    await bootstrap(anon, p)
    cookie = anon.cookies.jar
    assert any(c.path == f"{p}/" and c.name == "vds_session" for c in cookie)
    me = (await anon.get(f"{p}/api/v1/me")).json()
    assert me["role"] == "admin" and me["provider"] == "auth.local"
    again = await anon.post(
        f"{m}/bootstrap",
        json={"email": "x@vds.test", "display_name": "X", "password": PASSWORD},
        headers=SAME,
    )
    assert again.status_code == 409 and again.json()["code"] == "auth_local.already_initialized"
    assert (await anon.post(f"{m}/logout", headers=SAME)).status_code == 204
    assert (await anon.get(f"{p}/api/v1/me")).status_code == 401
    bad = await anon.post(
        f"{m}/login", json={"email": "root@vds.test", "password": "nope-nope-nope"}, headers=SAME
    )
    assert bad.status_code == 401 and bad.json()["code"] == "auth_local.invalid_credentials"
    ok = await anon.post(
        f"{m}/login",
        json={"email": "root@vds.test", "password": PASSWORD, "return_to": f"{p}/screens"},
        headers=SAME,
    )
    assert ok.status_code == 200 and ok.json()["redirect_to"] == f"{p}/screens"
    evil = await anon.post(
        f"{m}/login",
        json={"email": "root@vds.test", "password": PASSWORD, "return_to": "https://evil.test/"},
        headers=SAME,
    )
    assert evil.json()["redirect_to"] == f"{p}/"


async def test_editor_forbidden_on_admin_routes(
    admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    site = await admin.post(
        f"{p}/api/v1/sites", json={"name": "Moulins", "slug": "moulins"}, headers=SAME
    )
    assert site.status_code == 201
    sid = site.json()["id"]
    r = await editor.post(f"{p}/api/v1/sites", json={"name": "X", "slug": "x"}, headers=SAME)
    assert r.status_code == 403 and r.json()["code"] == "auth.forbidden"
    assert (await editor.get(f"{p}/api/v1/sites")).status_code == 200
    put = await editor.put(
        f"{p}/api/v1/config/site/{sid}/core/timezone", json={"value": "UTC"}, headers=SAME
    )
    assert put.status_code == 403
    users = await editor.post(
        f"{p}/api/v1/m/auth.local/users",
        json={"email": "z@vds.test", "display_name": "Z", "password": PASSWORD, "role": "admin"},
        headers=SAME,
    )
    assert users.status_code == 403
    assert (await editor.get(f"{p}/api/v1/config/site/{sid}/core")).status_code == 200


async def test_foreign_origin_refused(admin: AsyncClient, p: str) -> None:
    r = await admin.post(
        f"{p}/api/v1/sites",
        json={"name": "X", "slug": "x"},
        headers={"Origin": "https://evil.test"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "errors.origin_mismatch"
    r = await admin.post(
        f"{p}/api/v1/m/auth.local/login",
        json={"email": "root@vds.test", "password": PASSWORD},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403
    r = await admin.post(f"{p}/api/v1/m/auth.local/logout", headers={"Origin": "https://evil.test"})
    assert r.status_code == 403
    assert (await admin.get(f"{p}/api/v1/sites")).json() == []


async def test_inventory_config_and_audit(admin: AsyncClient, p: str, migrated_db_url: str) -> None:
    api = f"{p}/api/v1"
    site = (
        await admin.post(f"{api}/sites", json={"name": "Moulins", "slug": "moulins"}, headers=SAME)
    ).json()
    dup = await admin.post(f"{api}/sites", json={"name": "M2", "slug": "moulins"}, headers=SAME)
    assert dup.status_code == 409 and dup.json()["code"] == "site.slug_taken"
    group = (
        await admin.post(f"{api}/sites/{site['id']}/groups", json={"name": "Accueil"}, headers=SAME)
    ).json()
    screen = await admin.post(
        f"{api}/screens",
        json={"site_id": site["id"], "group_id": group["id"], "name": "S1"},
        headers=SAME,
    )
    assert screen.status_code == 201 and screen.json()["orientation"] == "landscape"
    sid = screen.json()["id"]
    assert (await admin.delete(f"{api}/sites/{site['id']}", headers=SAME)).status_code == 409
    assert (await admin.delete(f"{api}/groups/{group['id']}", headers=SAME)).json()[
        "code"
    ] == "group.not_empty"
    r = await admin.put(
        f"{api}/config/site/{site['id']}/core/timezone", json={"value": "UTC"}, headers=SAME
    )
    assert r.status_code == 204
    cfg = (await admin.get(f"{api}/config/screen/{sid}/core")).json()
    tz = cfg["values"]["timezone"]
    assert tz["value"] == "UTC" and tz["origin"]["level"] == "site"
    assert tz["origin"]["name"] == "Moulins"
    org_id = (await admin.get(f"{api}/organization")).json()["id"]
    mod = (await admin.get(f"{api}/config/organization/{org_id}/auth.local")).json()
    assert mod["values"]["session_ttl_hours"]["origin"] == {
        "level": "default",
        "id": None,
        "name": None,
    }
    assert (
        await admin.delete(f"{api}/config/site/{site['id']}/core/timezone", headers=SAME)
    ).status_code == 204
    assert (await admin.delete(f"{api}/screens/{sid}", headers=SAME)).status_code == 204
    assert (await admin.delete(f"{api}/groups/{group['id']}", headers=SAME)).status_code == 204
    assert (await admin.delete(f"{api}/sites/{site['id']}", headers=SAME)).status_code == 204
    actions = await audit_actions(migrated_db_url)
    for expected in (
        "user.created",
        "auth.bootstrap",
        "site.created",
        "group.created",
        "screen.created",
        "config.updated",
        "config.reset",
        "screen.deleted",
        "site.deleted",
    ):
        assert expected in actions, expected


async def test_login_logout_audited(anon: AsyncClient, p: str, migrated_db_url: str) -> None:
    await bootstrap(anon, p)
    m = f"{p}/api/v1/m/auth.local"
    await anon.post(f"{m}/logout", headers=SAME)
    await anon.post(
        f"{m}/login", json={"email": "root@vds.test", "password": "wrong-wrong-wrong"}, headers=SAME
    )
    await anon.post(
        f"{m}/login", json={"email": "root@vds.test", "password": PASSWORD}, headers=SAME
    )
    actions = await audit_actions(migrated_db_url)
    assert {"auth.logout", "auth.login_failed", "auth.login"} <= set(actions)


async def test_config_js_auth_urls(anon: AsyncClient, p: str) -> None:
    js = (await anon.get(f"{p}/config.js")).text
    assert f'"loginUrl": "{p}/login?return_to=' in js
    assert f'"logoutUrl": "{p}/api/v1/m/auth.local/logout"' in js
    assert '"links": []' in js


async def _login(c: AsyncClient, p: str, email: str, password: str) -> Any:
    return await c.post(
        f"{p}/api/v1/m/auth.local/login",
        json={"email": email, "password": password},
        headers=SAME,
    )


async def test_login_rate_limit_per_identifier(anon: AsyncClient, p: str) -> None:
    await bootstrap(anon, p)
    for _ in range(5):
        assert (await _login(anon, p, "Root@vds.test", "mauvais")).status_code == 401
    # même identifiant normalisé, même avec le bon mot de passe : bloqué
    r = await _login(anon, p, "ROOT@vds.test", PASSWORD)
    assert r.status_code == 429
    assert r.json()["code"] == "errors.auth.too_many_attempts"
    assert 1 <= int(r.headers["Retry-After"]) <= 15 * 60
    # un autre identifiant n'est pas bloqué par l'identifiant (seul le quota IP compte)
    assert (await _login(anon, p, "autre@vds.test", "x")).status_code == 401


async def test_login_rate_limit_per_ip(anon: AsyncClient, p: str) -> None:
    await bootstrap(anon, p)
    for i in range(20):
        assert (await _login(anon, p, f"u{i}@vds.test", "x")).status_code == 401
    r = await _login(anon, p, "root@vds.test", PASSWORD)
    assert r.status_code == 429 and "Retry-After" in r.headers


async def test_success_resets_identifier_failures(anon: AsyncClient, p: str) -> None:
    await bootstrap(anon, p)
    for _ in range(4):
        await _login(anon, p, "root@vds.test", "mauvais")
    assert (await _login(anon, p, "root@vds.test", PASSWORD)).status_code == 200
    for _ in range(4):
        assert (await _login(anon, p, "root@vds.test", "mauvais")).status_code == 401


async def test_users_provision_permission_gates_facade(app: FastAPI) -> None:
    from vds_sdk import ModuleManifest, PermissionDenied, load_manifest
    from vds_server.core.facade import ModuleCore

    rt: Runtime = app.state.runtime
    base = load_manifest(REPO_MODULES / "auth.local" / "module.json").model_dump()
    assert "users.provision" in base["permissions"]
    ModuleCore(rt, ModuleManifest.model_validate(base)).users  # noqa: B018
    base["permissions"] = [x for x in base["permissions"] if x != "users.provision"]
    with pytest.raises(PermissionDenied):
        ModuleCore(rt, ModuleManifest.model_validate(base)).users  # noqa: B018


# --- Critères de fin du lot 1 sur l'application réelle, aux deux préfixes ---


async def test_kiosk_headers_on_real_app(anon: AsyncClient, admin: AsyncClient, p: str) -> None:
    for path in (f"{p}/health", f"{p}/config.js", f"{p}/api/v1/me", f"{p}/api/v1/nope", f"{p}/x"):
        r = await admin.get(path)
        assert r.headers["x-robots-tag"] == "noindex", path
        assert r.headers["x-frame-options"] == "DENY", path
        assert r.headers["cache-control"] == "private, no-store", path


async def test_anonymous_cannot_read_park(anon: AsyncClient, p: str) -> None:
    for path in ("/api/v1/sites", "/api/v1/screens", "/api/v1/organization", "/api/v1/me"):
        assert (await anon.get(f"{p}{path}")).status_code == 401, path


async def test_theme_assets_served_under_prefix(anon: AsyncClient, p: str) -> None:
    for theme in ("theme.vtech", "theme.allia"):
        r = await anon.get(f"{p}/modules/{theme}/assets/theme.css")
        assert r.status_code == 200, theme
        assert r.headers["cache-control"] == "private, no-store"
    assert (await anon.get(f"{p}/modules/theme.vtech/assets/../module.json")).status_code == 404
    assert (await anon.get(f"{p}/modules/theme.vtech/assets/%2e%2e/module.json")).status_code == 404
    assert (await anon.get(f"{p}/modules/auth.local/assets/x")).status_code == 404


async def test_player_not_served_in_lot1(anon: AsyncClient, p: str) -> None:
    assert (await anon.get(f"{p}/player/")).status_code == 404


async def test_four_level_inheritance_and_return_to_inheritance(admin: AsyncClient, p: str) -> None:
    api = f"{p}/api/v1"
    org = (await admin.get(f"{api}/organization")).json()["id"]
    site = (await admin.post(f"{api}/sites", json={"name": "S", "slug": "s"}, headers=SAME)).json()
    grp = (
        await admin.post(f"{api}/sites/{site['id']}/groups", json={"name": "G"}, headers=SAME)
    ).json()
    scr = (
        await admin.post(
            f"{api}/screens",
            json={"site_id": site["id"], "group_id": grp["id"], "name": "E"},
            headers=SAME,
        )
    ).json()

    async def tz() -> dict[str, object]:
        r = await admin.get(f"{api}/config/screen/{scr['id']}/core")
        return r.json()["values"]["timezone"]  # type: ignore[no-any-return]

    async def put(level: str, tid: str, value: str) -> None:
        r = await admin.put(
            f"{api}/config/{level}/{tid}/core/timezone", json={"value": value}, headers=SAME
        )
        assert r.status_code == 204

    async def reset(level: str, tid: str) -> None:
        r = await admin.delete(f"{api}/config/{level}/{tid}/core/timezone", headers=SAME)
        assert r.status_code == 204

    assert (await tz())["origin"]["level"] == "default"  # type: ignore[index]
    chain = [
        ("organization", org, "UTC", "organization"),
        ("site", site["id"], "Europe/London", "site"),
        ("group", grp["id"], "Europe/Berlin", "group"),
        ("screen", scr["id"], "America/Cayenne", "screen"),
    ]
    for level, tid, value, expected in chain:
        await put(level, str(tid), value)
        got = await tz()
        assert got["value"] == value and got["origin"]["level"] == expected  # type: ignore[index]
    rev = chain[::-1]
    for (level, tid, _, _), (_, _, value, expected) in zip(rev[:-1], rev[1:], strict=True):
        await reset(level, str(tid))
        got = await tz()
        assert got["value"] == value and got["origin"]["level"] == expected  # type: ignore[index]
    await reset("organization", str(org))
    assert (await tz())["origin"]["level"] == "default"  # type: ignore[index]
    # Valeur invalide refusée, rien n'est stocké.
    bad = await admin.put(
        f"{api}/config/site/{site['id']}/core/locale", json={"value": "de"}, headers=SAME
    )
    assert bad.status_code in (400, 422)


async def test_editor_reads_but_cannot_write_config(
    admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    api = f"{p}/api/v1"
    org = (await admin.get(f"{api}/organization")).json()["id"]
    assert (await editor.get(f"{api}/config/organization/{org}/core")).status_code == 200
    r = await editor.put(
        f"{api}/config/organization/{org}/core/timezone", json={"value": "UTC"}, headers=SAME
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    ("theme", "provider"), [("theme.vtech", "auth.local"), ("theme.allia", "auth.local")]
)
async def test_theme_switch_in_config_js(
    migrated_db_url: str, clean: None, base: str, theme: str, provider: str
) -> None:
    settings = Settings(
        base_path=base,
        database_url=migrated_db_url,
        public_origin=ORIGIN,
        modules_dir=REPO_MODULES,
        theme=theme,
        auth_provider=provider,
    )
    runtime = Runtime(settings)
    application: FastAPI = create_app(settings, runtime=runtime)
    pre = "" if base == "/" else base
    async with runtime.lifespan(application):
        assert runtime.startup_ok()
        async with AsyncClient(transport=ASGITransport(app=application), base_url=ORIGIN) as c:
            js = (await c.get(f"{pre}/config.js")).text
            assert f'"id": "{theme}"' in js
            assert f'"cssUrl": "{pre}/modules/{theme}/assets/theme.css"' in js
            assert (await c.get(f"{pre}/modules/{theme}/assets/theme.css")).status_code == 200


def test_i18n_theme_dirs_exist() -> None:
    for m in ("theme.vtech", "theme.allia", "auth.local"):
        assert (Path(REPO_MODULES) / m / "i18n").is_dir()
