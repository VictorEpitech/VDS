"""Tests du module d'authentification SSO `auth.studio-allia` (mode hébergé Studio Allia)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from vds_sdk import AccessDenied
from vds_sdk.manifest import load_manifest
from vds_sdk.server import AuthRequest
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.main import create_app
from vds_server.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = REPO_ROOT / "modules" / "auth.studio-allia"
REPO_MODULES = REPO_ROOT / "modules"
ORIGIN = "https://allia-innovation.fr"
SECRET = "test-studio-shared-secret-xyz"

import sys
sys.path.insert(0, str(MODULE_DIR))
from server.module import StudioAlliaAuth, COOKIE_NAME


def test_manifest_valid() -> None:
    manifest = load_manifest(MODULE_DIR / "module.json")
    assert manifest.id == "auth.studio-allia"
    assert manifest.kind == "auth-provider"
    assert manifest.entrypoints.get("server") == "server.module:StudioAlliaAuth"
    assert "users.provision" in manifest.permissions
    assert "auth.sso" in manifest.capabilities


def test_login_and_logout_urls() -> None:
    auth = StudioAlliaAuth()
    login_url = auth.login_url("/espace/affichage/screens")
    assert login_url.startswith("/espace/connexion?retour=")
    assert "%2Fespace%2Faffichage%2Fscreens" in login_url

    assert auth.logout_url() == "/espace/deconnexion"

    links = auth.shell_links()
    assert len(links) == 1
    assert links[0].id == "back_to_studio"
    assert links[0].url == "/espace"
    assert links[0].label_key == "nav.links.back_to_studio"


@pytest.mark.asyncio
async def test_authenticate_no_cookie() -> None:
    auth = StudioAlliaAuth()
    req = AuthRequest(method="GET", path="/", cookies={})
    assert await auth.authenticate(req) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("studio_role", "expected_vds_role"),
    [
        ("president", "admin"),
        ("direction", "admin"),
        ("membre", "editor"),
    ],
)
async def test_authenticate_valid_roles(
    studio_role: str, expected_vds_role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUDIO_SHARED_SECRET", SECRET)
    auth = StudioAlliaAuth()

    fake_response = httpx.Response(
        status_code=200,
        json={"id": "usr_123", "nom": "Camille Allia", "email": "camille@allia.fr", "role": studio_role},
        request=httpx.Request("GET", "http://fake"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_response
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_val_1"})
        identity = await auth.authenticate(req)

        assert identity is not None
        assert identity.provider == "auth.studio-allia"
        assert identity.external_id == "usr_123"
        assert identity.display_name == "Camille Allia"
        assert identity.email == "camille@allia.fr"
        assert identity.role == expected_vds_role

        mock_get.assert_called_once()
        headers = mock_get.call_args[1]["headers"]
        assert headers["Cookie"] == f"{COOKIE_NAME}=cookie_val_1"
        assert headers["x-vds-secret"] == SECRET


@pytest.mark.asyncio
async def test_authenticate_financeur_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUDIO_SHARED_SECRET", SECRET)
    auth = StudioAlliaAuth()

    fake_response = httpx.Response(
        status_code=200,
        json={"id": "usr_fin", "nom": "Banquière", "email": "fin@allia.fr", "role": "financeur"},
        request=httpx.Request("GET", "http://fake"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_response
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_val_fin"})
        with pytest.raises(AccessDenied) as exc:
            await auth.authenticate(req)
        assert "financeur_forbidden" in exc.value.code


@pytest.mark.asyncio
async def test_authenticate_unauthorized_role(monkeypatch: pytest.MonkeyPatch) -> None:
    auth = StudioAlliaAuth()
    fake_response = httpx.Response(
        status_code=200,
        json={"id": "usr_x", "nom": "Invité", "email": "guest@allia.fr", "role": "guest"},
        request=httpx.Request("GET", "http://fake"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_response
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_guest"})
        with pytest.raises(AccessDenied) as exc:
            await auth.authenticate(req)
        assert "role_unauthorized" in exc.value.code


@pytest.mark.asyncio
async def test_authenticate_401_from_studio() -> None:
    auth = StudioAlliaAuth()
    fake_response = httpx.Response(status_code=401, request=httpx.Request("GET", "http://fake"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_response
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_expired"})
        assert await auth.authenticate(req) is None


@pytest.mark.asyncio
async def test_authenticate_403_from_studio() -> None:
    auth = StudioAlliaAuth()
    fake_response = httpx.Response(status_code=403, request=httpx.Request("GET", "http://fake"))

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_response
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_bad_secret"})
        with pytest.raises(AccessDenied) as exc:
            await auth.authenticate(req)
        assert "secret_mismatch" in exc.value.code


@pytest.mark.asyncio
async def test_authenticate_network_down_raises_503() -> None:
    auth = StudioAlliaAuth()
    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Connection refused")):
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_net"})
        with pytest.raises(ApiError) as exc:
            await auth.authenticate(req)
        assert exc.value.status == 503
        assert exc.value.code == "auth.provider_unavailable"


@pytest.mark.asyncio
async def test_authenticate_cache_60s() -> None:
    auth = StudioAlliaAuth()
    fake_response = httpx.Response(
        status_code=200,
        json={"id": "u1", "nom": "Cached User", "email": "u1@allia.fr", "role": "membre"},
        request=httpx.Request("GET", "http://fake"),
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_response
        req = AuthRequest(method="GET", path="/", cookies={COOKIE_NAME: "cookie_cache"})
        id1 = await auth.authenticate(req)
        assert id1 is not None

        # Deuxième appel identique dans la fenêtre de cache -> aucun nouvel appel HTTP
        id2 = await auth.authenticate(req)
        assert id2 is not None
        assert id2.external_id == "u1"
        assert mock_get.call_count == 1


@pytest.mark.asyncio
async def test_same_origin_csrf_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VDS_PUBLIC_ORIGIN", ORIGIN)
    auth = StudioAlliaAuth()

    # 1. Sec-Fetch-Site cross-site sur POST -> rejeté
    req_cross = AuthRequest(
        method="POST",
        path="/api/v1/screens",
        cookies={COOKIE_NAME: "c1"},
        headers={"sec-fetch-site": "cross-site", "origin": ORIGIN},
    )
    with pytest.raises(AccessDenied) as exc:
        await auth.authenticate(req_cross)
    assert "cross_site_forbidden" in exc.value.code

    # 2. Origin pirate sur POST -> rejeté
    req_bad_origin = AuthRequest(
        method="POST",
        path="/api/v1/screens",
        cookies={COOKIE_NAME: "c1"},
        headers={"origin": "https://evil-site.com"},
    )
    with pytest.raises(AccessDenied) as exc:
        await auth.authenticate(req_bad_origin)
    assert "origin_mismatch" in exc.value.code

    # 3. Origin identique sur POST -> accepté
    fake_resp = httpx.Response(
        status_code=200,
        json={"id": "u1", "nom": "User", "role": "president"},
        request=httpx.Request("GET", "http://fake"),
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = fake_resp
        req_same = AuthRequest(
            method="POST",
            path="/api/v1/screens",
            cookies={COOKIE_NAME: "c1"},
            headers={"origin": ORIGIN, "host": "allia-innovation.fr"},
        )
        ident = await auth.authenticate(req_same)
        assert ident is not None
        assert ident.role == "admin"


# --- Test d'intégration serveur complet ---

async def _truncate(url: str) -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(
            text(
                "TRUNCATE audit_log, config_override, screen, screen_group,"
                " site, user_account, organization, event_outbox CASCADE"
            )
        )
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not __import__("os").environ.get("VDS_TEST_DATABASE_URL"),
    reason="VDS_TEST_DATABASE_URL requis pour le test d'intégration DB",
)
async def test_integration_studio_allia_server(migrated_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    await _truncate(migrated_db_url)
    monkeypatch.setenv("STUDIO_SHARED_SECRET", SECRET)
    monkeypatch.setenv("STUDIO_SESSION_URL", "http://studio.test/api/espace/vds/session")

    settings = Settings(
        base_path="/espace/affichage",
        database_url=migrated_db_url,
        public_origin=ORIGIN,
        modules_dir=REPO_MODULES,
        auth_provider="auth.studio-allia",
        theme="theme.allia",
    )
    runtime = Runtime(settings)
    app = create_app(settings, runtime=runtime)

    async with runtime.lifespan(app):
        assert runtime.startup_ok(), runtime.registry.startup_errors

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            # 1. Shell config renvoie les liens Allia
            res_shell = await client.get("/espace/affichage/api/v1/shell")
            assert res_shell.status_code == 200
            data_shell = res_shell.json()
            assert data_shell["logoutUrl"] == "/espace/deconnexion"
            assert "/espace/connexion" in data_shell["loginUrl"]
            assert any(link["id"] == "back_to_studio" for link in data_shell["links"])

            # 2. Sans cookie -> 401 unauthenticated
            res_me_anon = await client.get("/espace/affichage/api/v1/me")
            assert res_me_anon.status_code == 401

            # 3. Avec cookie et session Studio -> 200 et utilisateur créé en base
            fake_session = httpx.Response(
                status_code=200,
                json={"id": "pres_42", "nom": "Présidente Jeanne", "email": "jeanne@allia.fr", "role": "president"},
                request=httpx.Request("GET", "http://fake"),
            )
            with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
                mock_get.return_value = fake_session
                client.cookies.set(COOKIE_NAME, "valid_cookie_session_42")
                res_me_auth = await client.get("/espace/affichage/api/v1/me")
                assert res_me_auth.status_code == 200
                me = res_me_auth.json()
                assert me["display_name"] == "Présidente Jeanne"
                assert me["email"] == "jeanne@allia.fr"
                assert me["role"] == "admin"
                assert me["provider"] == "auth.studio-allia"
