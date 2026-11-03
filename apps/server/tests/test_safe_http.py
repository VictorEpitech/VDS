"""SafeHttp (anti-SSRF) : adresses interdites, rebinding, redirections, plafonds."""

import asyncio

import httpx
import pytest
from vds_sdk.server.http import SafeHttpError
from vds_server.net.safe_http import CoreSafeHttp, is_public_ip

ORIGIN = "https://vds.example.org"


def make(
    table: dict[str, list[list[str]]] | None = None,
    handler: httpx.MockTransport | None = None,
) -> tuple[CoreSafeHttp, list[httpx.Request], dict[str, int]]:
    """`table[host]` = liste de réponses DNS successives (la dernière se répète)."""
    seen: list[httpx.Request] = []
    calls: dict[str, int] = {}
    answers = table or {"feed.example.com": [["93.184.216.34"]]}

    async def resolver(host: str, port: int) -> list[str]:
        n = calls.get(host, 0)
        calls[host] = n + 1
        if host not in answers:
            raise OSError("nxdomain")
        seq = answers[host]
        return seq[min(n, len(seq) - 1)]

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<rss/>", headers={"content-type": "application/xml"})

    transport = handler or httpx.MockTransport(ok)
    return (
        CoreSafeHttp(version="1.2.3", public_origin=ORIGIN, resolver=resolver, transport=transport),
        seen,
        calls,
    )


async def code_of(http: CoreSafeHttp, url: str, **kw: object) -> str:
    with pytest.raises(SafeHttpError) as exc:
        await http.get(url, max_bytes=1000, **kw)  # type: ignore[arg-type]
    return exc.value.code


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
        "0.0.0.0", # noqa: S104
        "224.0.0.1", "240.0.0.1", "::1", "fc00::1", "fd12::1", "fe80::1", "ff02::1",
        "::ffff:10.0.0.1", "::ffff:8.8.8.8", "2002:0a00:0001::", "64:ff9b::a00:1", "::",
    ],
)  # fmt: skip
def test_non_public_addresses(ip: str) -> None:
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1::1"])
def test_public_addresses(ip: str) -> None:
    assert is_public_ip(ip)


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x", "https://10.0.0.5/x", "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x", "https://[fc00::1]/x", "https://[::ffff:127.0.0.1]/x",
        "https://100.64.0.9/x",
    ],
)  # fmt: skip
async def test_literal_blocked(url: str) -> None:
    http, seen, _ = make()
    assert await code_of(http, url) == "net.blocked_address"
    assert not seen


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://feed.example.com/x", "net.scheme_refused"),
        ("ftp://feed.example.com/x", "net.scheme_refused"),
        ("file:///etc/passwd", "net.scheme_refused"),
        ("https://feed.example.com:8443/x", "net.scheme_refused"),
        ("https://user:pw@feed.example.com/x", "net.scheme_refused"),
        ("https:///x", "net.scheme_refused"),
        ("https://vds.example.org/api", "net.blocked_address"),
        ("https://VDS.example.org./api", "net.blocked_address"),
    ],
)
async def test_refused_urls(url: str, code: str) -> None:
    http, seen, _ = make()
    assert await code_of(http, url) == code
    assert not seen


async def test_host_resolving_to_private_blocked() -> None:
    http, seen, _ = make({"evil.example.com": [["10.0.0.7"]], "localhost": [["127.0.0.1"]]})
    assert await code_of(http, "https://evil.example.com/") == "net.blocked_address"
    assert await code_of(http, "https://localhost/") == "net.blocked_address"
    assert not seen


async def test_any_private_among_many_blocks() -> None:
    http, seen, _ = make({"mix.example.com": [["93.184.216.34", "192.168.0.1"]]})
    assert await code_of(http, "https://mix.example.com/") == "net.blocked_address"
    assert not seen


async def test_dns_failure() -> None:
    http, _, _ = make()
    assert await code_of(http, "https://unknown.example.com/") == "net.dns_failed"


async def test_rebinding_second_resolution_private() -> None:
    """La 2e résolution (privée) n'est jamais utilisée : on se connecte à l'IP vérifiée ;
    et si le serveur redirige vers le même hôte devenu privé, la revérification refuse."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://rebind.example.com/next"})
        return httpx.Response(200, content=b"x")

    http, _, calls = make(
        {"rebind.example.com": [["93.184.216.34"], ["10.0.0.1"]]}, httpx.MockTransport(handler)
    )
    assert await code_of(http, "https://rebind.example.com/start") == "net.blocked_address"
    assert calls["rebind.example.com"] == 2


async def test_connects_to_pinned_ip_with_original_host_and_sni() -> None:
    http, seen, _ = make()
    resp = await http.get(
        "https://feed.example.com/a/b?x=1",
        max_bytes=1000,
        headers={"User-Agent": "contact@example.org", "Cookie": "s=1", "Host": "evil"},
        accept=("application/xml",),
    )
    req = seen[0]
    assert resp.status == 200 and resp.body == b"<rss/>"
    assert req.url.host == "93.184.216.34" and req.url.path == "/a/b" and req.url.query == b"x=1"
    assert req.headers["host"] == "feed.example.com"
    assert req.extensions["sni_hostname"] == "feed.example.com"
    assert req.headers["user-agent"] == f"VDS/1.2.3 (+{ORIGIN}) contact@example.org"
    assert "cookie" not in req.headers and req.headers["accept"] == "application/xml"


async def test_redirects_revalidated() -> None:
    def redir(target: str) -> httpx.MockTransport:
        return httpx.MockTransport(
            lambda r: (
                httpx.Response(302, headers={"location": target})
                if r.url.path == "/"
                else httpx.Response(200, content=b"ok")
            )
        )

    table = {"feed.example.com": [["93.184.216.34"]], "other.example.com": [["8.8.8.8"]]}
    for target, code in [
        ("https://127.0.0.1/", "net.blocked_address"),
        ("https://169.254.169.254/latest", "net.blocked_address"),
        ("https://[::1]/", "net.blocked_address"),
        ("http://other.example.com/x", "net.redirect_refused"),
        ("https://vds.example.org/", "net.blocked_address"),
    ]:
        http, _, _ = make(table, redir(target))
        assert await code_of(http, "https://feed.example.com/") == code, target
    http, _, _ = make(table, redir("/ok"))  # relative
    assert (await http.get("https://feed.example.com/", max_bytes=100)).body == b"ok"


async def test_redirect_loop_limited() -> None:
    n = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(302, headers={"location": "https://feed.example.com/"})

    http, _, _ = make(handler=httpx.MockTransport(handler))
    assert await code_of(http, "https://feed.example.com/") == "net.redirect_refused"
    assert n == 4  # requête initiale + 3 redirections


async def test_too_large_declared_and_streamed() -> None:
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000))
    http, _, _ = make(handler=big)
    assert await code_of(http, "https://feed.example.com/") == "net.too_large"

    async def gen() -> object:  # corps en flux sans Content-Length
        for _ in range(50):
            yield b"y" * 100

    stream = httpx.MockTransport(lambda r: httpx.Response(200, content=gen()))  # type: ignore[arg-type]
    http, _, _ = make(handler=stream)
    assert await code_of(http, "https://feed.example.com/") == "net.too_large"


async def test_hard_cap_applies_to_max_bytes() -> None:
    body = b"z" * (2 * 1024 * 1024 + 1)
    http, _, _ = make(handler=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    with pytest.raises(SafeHttpError) as exc:
        await http.get("https://feed.example.com/", max_bytes=10**9)
    assert exc.value.code == "net.too_large"


async def test_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200)

    http, _, _ = make(handler=httpx.MockTransport(slow))
    assert await code_of(http, "https://feed.example.com/", timeout_s=0.2) == "net.timeout"


async def test_http_status_and_content_type() -> None:
    http, _, _ = make(handler=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert await code_of(http, "https://feed.example.com/") == "net.http_status"
    http, _, _ = make()
    assert await code_of(http, "https://feed.example.com/", accept=("text/html",)) == (
        "net.content_type_refused"
    )


async def test_set_cookie_not_exposed_and_no_cookie_jar() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/":
            return httpx.Response(302, headers={"location": "/b", "set-cookie": "sid=1; Path=/"})
        return httpx.Response(200, content=b"k", headers={"set-cookie": "a=b"})

    http, _, _ = make(handler=httpx.MockTransport(handler))
    resp = await http.get("https://feed.example.com/", max_bytes=10)
    assert "set-cookie" not in resp.headers and "cookie" not in sent[1].headers
