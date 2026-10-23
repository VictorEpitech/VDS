# ruff: noqa: F811
"""Backlog sécurité mineur (L3-T0) : items 6, 7, 8, 11, aux deux VDS_BASE_PATH."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, new_client, p  # noqa: F401
from test_diffusion import mk_screen
from test_pairing import DEVICE, _pairing_clean, hdr, pair, sql, start  # noqa: F401
from vds_server.main import app_factory, create_app
from vds_server.middleware.upload_limit import OVERHEAD
from vds_server.security.client_ip import limiter_key
from vds_server.settings import Settings

BASES = ["/", "/espace/affichage"]
EVIL = {"Origin": "https://evil.test"}


def pre(b: str) -> str:
    return "" if b == "/" else b


@pytest.fixture(params=BASES)
def sbase(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def small(sbase: str, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    (tmp_path / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    settings = Settings(base_path=sbase, admin_dir=tmp_path, public_origin="https://vds.test")
    router = APIRouter(prefix="/api/v1")
    seen = {"media": 0}

    @router.post("/player/heartbeat")
    async def hb() -> dict[str, bool]:
        return {"ok": True}

    @router.post("/agent/x")
    async def ag() -> dict[str, bool]:
        return {"ok": True}

    @router.post("/admin-thing")
    async def adm() -> dict[str, bool]:
        return {"ok": True}

    @router.post("/media")
    async def media() -> dict[str, int]:
        seen["media"] += 1
        return seen

    small_app = create_app(settings, [router])
    # le plafond est fourni par le runtime ; ici fixe à 1000 octets (+ marge)
    from vds_server.middleware.upload_limit import UploadLimitMiddleware

    async def limit() -> int:
        return 1000

    small_app.add_middleware(UploadLimitMiddleware, settings=settings, limit=limit)
    async with AsyncClient(
        transport=ASGITransport(app=small_app), base_url="https://vds.test"
    ) as c:
        yield c


# --- 6 ---------------------------------------------------------------------
async def test_bearer_exempts_only_token_routes(small: AsyncClient, sbase: str) -> None:
    b = {**EVIL, "Authorization": "Bearer vds_dev_x"}
    for path in ("/player/heartbeat", "/agent/x"):
        assert (await small.post(f"{pre(sbase)}/api/v1{path}", headers=b)).status_code == 200
    r = await small.post(f"{pre(sbase)}/api/v1/admin-thing", headers=b)
    assert r.status_code == 403 and r.json()["error"]["code"] == "errors.origin_mismatch"
    r = await small.post(f"{pre(sbase)}/api/v1/player/heartbeat", headers=EVIL)
    assert r.status_code == 403


# --- 7 ---------------------------------------------------------------------
def test_limiter_key_ipv6_prefix() -> None:
    assert limiter_key("203.0.113.9") == "203.0.113.9"
    a = limiter_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    assert a == limiter_key("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert a != limiter_key("2001:db8:1:3::1")
    assert limiter_key("::ffff:203.0.113.9") == "203.0.113.9"
    assert limiter_key("unknown") == "unknown"


def test_wildcard_forwarded_allow_ips_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    with pytest.raises(RuntimeError, match="forwarded_allow_ips_wildcard"):
        app_factory()


async def test_expired_pairing_requests_purged(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    async with new_client(app) as player:
        await start(player, p)
        await sql(
            migrated_db_url,
            "UPDATE pairing_request SET expires_at = now() - interval '2 hours'",
        )
        fresh = await start(player, p)
    rows = await sql(migrated_db_url, "SELECT id FROM pairing_request")
    assert [str(r[0]) for r in rows] == [fresh["pairing_id"]]


# --- 8 ---------------------------------------------------------------------
async def test_multipart_size_checked_before_read(small: AsyncClient, sbase: str) -> None:
    url = f"{pre(sbase)}/api/v1/media"
    big = {"file": ("a.bin", b"x" * (1000 + OVERHEAD + 10))}
    r = await small.post(url, files=big, headers=SAME)
    assert r.status_code == 413 and r.json()["code"] == "media.too_large"
    ok = await small.post(url, files={"file": ("a.bin", b"x" * 100)}, headers=SAME)
    assert ok.status_code == 200 and ok.json() == {"media": 1}  # seul l'envoi valide arrive


async def test_multipart_without_length_refused(small: AsyncClient, sbase: str) -> None:
    async def body() -> AsyncIterator[bytes]:
        yield b"x" * 10

    r = await small.post(f"{pre(sbase)}/api/v1/media", content=body(), headers=SAME)
    assert r.status_code == 411 and r.json()["code"] == "media.length_required"


# --- 11 --------------------------------------------------------------------
async def test_device_token_expiry_set_slides_and_expires(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        rows = await sql(
            migrated_db_url,
            "SELECT expires_at > now() + interval '89 days' FROM screen_device",
        )
        assert rows[0][0] is True
        hb = f"{p}/api/v1/player/heartbeat"
        # proche de l'expiration : le heartbeat prolonge
        await sql(migrated_db_url, "UPDATE screen_device SET expires_at = now() + interval '1 day'")
        assert (await player.post(hb, json={}, headers=hdr(token))).status_code == 200
        rows = await sql(
            migrated_db_url,
            "SELECT expires_at > now() + interval '89 days' FROM screen_device",
        )
        assert rows[0][0] is True
        # expiré : refusé, le player revient à l'appairage
        await sql(migrated_db_url, "UPDATE screen_device SET expires_at = now() - interval '1 s'")
        r = await player.post(hb, json={}, headers=hdr(token))
        assert r.status_code == 401 and r.json()["code"] == "errors.device_revoked"
