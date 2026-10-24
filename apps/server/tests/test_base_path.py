from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from vds_server.main import create_app
from vds_server.security.cookies import cookie_path, set_cookie
from vds_server.security.return_to import safe_return_to
from vds_server.settings import Settings

BASES = ["/", "/espace/affichage"]


def _prefix(base: str) -> str:
    return "" if base == "/" else base


@pytest.fixture(params=BASES)
def base(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def settings(base: str, tmp_path: Path) -> Settings:
    (tmp_path / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    (tmp_path / "app.js").write_text("1")
    return Settings(base_path=base, admin_dir=tmp_path, public_origin="https://vds.test")


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    router = APIRouter(prefix="/api/v1")

    @router.post("/_probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/_cookie")
    async def cookie(request: Request) -> Response:
        r = Response()
        set_cookie(r, settings, request, "sid", "v")
        return r

    return create_app(settings, [router])


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://vds.test") as c:
        yield c


async def test_outside_prefix_404(client: AsyncClient, base: str) -> None:
    if base == "/":
        pytest.skip("pas de hors-préfixe à la racine")
    for path in ("/health", "/", "/config.js", "/api/v1/me"):
        r = await client.get(path)
        assert r.status_code == 404
        assert r.headers["cache-control"] == "private, no-store"


async def test_headers_everywhere(client: AsyncClient, base: str) -> None:
    p = _prefix(base)
    for path in (
        f"{p}/health",
        f"{p}/config.js",
        f"{p}/app.js",
        f"{p}/api/v1/nope",
        f"{p}/media/x/y",
    ):
        r = await client.get(path)
        assert r.headers["x-robots-tag"] == "noindex"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["cache-control"] == "private, no-store"


async def test_config_js(client: AsyncClient, base: str) -> None:
    r = await client.get(f"{_prefix(base)}/config.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert f'"apiBase": "{_prefix(base)}/api/v1"' in r.text


async def test_spa_base_href_and_fallback(client: AsyncClient, base: str) -> None:
    p = _prefix(base)
    for path in (f"{p}/", f"{p}/screens/42"):
        r = await client.get(path)
        assert r.status_code == 200
        assert f'<base href="{p}/">' in r.text
    assert (await client.get(f"{p}/app.js")).text == "1"
    assert (await client.get(f"{p}/api/v1/unknown")).status_code == 404
    assert (await client.get(f"{p}/player/x")).status_code == 404


async def test_cookie_path(client: AsyncClient, base: str, settings: Settings) -> None:
    r = await client.get(f"{_prefix(base)}/api/v1/_cookie")
    header = r.headers["set-cookie"]
    assert f"Path={_prefix(base)}/" in header
    assert "HttpOnly" in header and "Secure" in header and "SameSite=lax" in header
    assert cookie_path(settings) == f"{_prefix(base)}/"


async def test_foreign_origin_403(client: AsyncClient, base: str) -> None:
    url = f"{_prefix(base)}/api/v1/_probe"
    for h in ({"Origin": "https://evil.test"}, {"Sec-Fetch-Site": "cross-site"}, {}):
        r = await client.post(url, headers=h)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "errors.origin_mismatch"


async def test_same_origin_ok_bearer_not_exempt_on_cookie_routes(
    client: AsyncClient, base: str
) -> None:
    url = f"{_prefix(base)}/api/v1/_probe"
    assert (await client.post(url, headers={"Origin": "https://vds.test"})).status_code == 200
    assert (await client.post(url, headers={"Sec-Fetch-Site": "same-origin"})).status_code == 200
    assert (await client.post(url, headers={"Authorization": "Bearer t"})).status_code == 403


def test_return_to(settings: Settings, base: str) -> None:
    p = _prefix(base)
    assert safe_return_to(f"{p}/screens?a=1", settings) == f"{p}/screens?a=1"
    bad: list[str | None] = [
        "https://evil.test/",
        "//evil.test/",
        "/\\evil.test",
        f"{p}/../x",
        "javascript:alert(1)",
        "",
        None,
        f"{p}/a\nb",
    ]
    if base != "/":
        bad += ["/other", p, f"{p}x/", "/"]
    for v in bad:
        assert safe_return_to(v, settings) == f"{p}/"


@pytest.mark.parametrize("bad", ["/a/", "a b", "/A", "//x"])
def test_invalid_base_path(bad: str) -> None:
    with pytest.raises(ValueError):
        Settings(base_path=bad)


@pytest.mark.parametrize(
    ("scheme", "origin", "mode", "secure"),
    [
        ("http", None, "auto", False),
        ("https", None, "auto", True),
        ("http", "https://vds.test", "auto", True),
        ("http", "http://192.168.1.182:8002", "auto", False),
        ("https", None, "false", False),
        ("http", None, "true", True),
    ],
)
async def test_cookie_secure_matrix(
    base: str, tmp_path: Path, scheme: str, origin: str | None, mode: str, secure: bool
) -> None:
    st = Settings.model_validate(
        {"base_path": base, "admin_dir": tmp_path, "public_origin": origin, "cookie_secure": mode}
    )
    router = APIRouter(prefix="/api/v1")

    @router.get("/_cookie")
    async def cookie(request: Request) -> Response:
        r = Response()
        set_cookie(r, st, request, "vds_session", "v")
        return r

    transport = ASGITransport(app=create_app(st, [router]))
    async with AsyncClient(transport=transport, base_url=f"{scheme}://host.test") as c:
        header = (await c.get(f"{_prefix(base)}/api/v1/_cookie")).headers["set-cookie"]
    assert ("Secure" in header) is secure
    assert header.startswith("vds_session=")  # pas de préfixe __Host-/__Secure-
    assert f"Path={_prefix(base)}/" in header


def test_startup_warning_http(caplog: pytest.LogCaptureFixture) -> None:
    from vds_server.security.cookies import warn_if_insecure_http

    with caplog.at_level("WARNING", logger="vds.security"):
        warn_if_insecure_http(Settings(public_origin="http://192.168.1.182:8002"))
    assert "security.http_cookies_not_secure_unless_https_request" in caplog.text
    caplog.clear()
    with caplog.at_level("WARNING", logger="vds.security"):
        warn_if_insecure_http(Settings(public_origin="https://vds.test"))
    assert not caplog.text
