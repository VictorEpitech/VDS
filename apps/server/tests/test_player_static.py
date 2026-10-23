"""Build du player sous `<base>/player/` et scripts des content-types (deux VDS_BASE_PATH)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from vds_server.main import create_app
from vds_server.settings import Settings

BASES = ["/", "/espace/affichage"]
MODULES = Path(__file__).resolve().parents[3] / "modules"


@pytest.fixture(params=BASES)
def base(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def app(base: str, tmp_path: Path) -> FastAPI:
    player = tmp_path / "player"
    (player / "assets").mkdir(parents=True)
    (player / "index.html").write_text(
        '<html><head><script src="../config.js"></script></head><body><div id="app"></div>'
        "</body></html>"
    )
    (player / "assets" / "app.js").write_text("1")
    (player / "sw.js").write_text("self")
    admin = tmp_path / "admin"
    admin.mkdir()
    (admin / "index.html").write_text("<html><head></head><body>admin</body></html>")
    settings = Settings(base_path=base, admin_dir=admin, player_dir=player, modules_dir=MODULES)
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://vds.test") as c:
        yield c


def _p(base: str) -> str:
    return "" if base == "/" else base


async def test_player_index_has_base_href_and_csp(client: AsyncClient, base: str) -> None:
    r = await client.get(f"{_p(base)}/player/")
    assert r.status_code == 200
    assert f'<base href="{_p(base)}/player/">' in r.text
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "media-src 'self' blob:" in csp
    assert "frame-src https:" in csp
    assert r.headers["cache-control"] == "private, no-store"


async def test_player_assets_and_sw(client: AsyncClient, base: str) -> None:
    p = _p(base)
    assert (await client.get(f"{p}/player/assets/app.js")).text == "1"
    assert (await client.get(f"{p}/player/sw.js")).status_code == 200
    assert (await client.get(f"{p}/player/inconnu/chemin")).status_code == 404
    escape = await client.get(f"{p}/player/..%2f..%2fetc/passwd")
    assert escape.status_code == 404


async def test_player_redirect_keeps_prefix(client: AsyncClient, base: str) -> None:
    r = await client.get(f"{_p(base)}/player")
    assert r.status_code == 307
    assert r.headers["location"] == f"{_p(base)}/player/"


async def test_content_scripts_public_but_not_other_modules(client: AsyncClient, base: str) -> None:
    p = _p(base)
    r = await client.get(f"{p}/modules/content.image/assets/player.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert (await client.get(f"{p}/modules/auth.local/assets/x.js")).status_code == 404
    up = await client.get(f"{p}/modules/content.image/assets/..%2fmodule.json")
    assert up.status_code == 404


async def test_admin_unaffected(client: AsyncClient, base: str) -> None:
    r = await client.get(f"{_p(base)}/")
    assert r.status_code == 200 and "admin" in r.text


async def test_admin_csp_strict_on_both_bases(client: AsyncClient, base: str) -> None:
    p = _p(base)
    for url in (f"{p}/", f"{p}/screens", f"{p}/power/plans"):
        r = await client.get(url)
        assert r.status_code == 200
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert "connect-src 'self'" in csp and "object-src 'none'" in csp
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert f'<base href="{p}/">' in r.text
