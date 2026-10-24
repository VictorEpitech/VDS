import asyncio
import hashlib
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from helpers import FakeServer, cmd_frame, eventually
from test_runtime import TOKEN, fast, make_agent, plan
from vds_agent.cache import MediaCache
from vds_agent.cascade import Cascade
from vds_agent.lan import LanServer, parse_range, signed_path
from vds_agent.runtime import Agent
from vds_agent.settings import AgentSettings
from vds_agent.store import Store

BASE = "https://h.example/espace/affichage"
A = b"alpha" * 1000
B = b"bravo" * 500


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def entry(b: bytes, media: str = "a" * 32, variant: str = "original") -> dict[str, Any]:
    return {
        "media_id": media,
        "variant": variant,
        "sha256": sha(b),
        "bytes": len(b),
        "path": f"media/{media}/{variant}",
    }


class Origin:
    """Faux serveur HTTP (MockTransport) : manifeste + médias, compte les téléchargements."""

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs  # chemin relatif -> octets servis
        self.manifest: dict[str, Any] = {"items": []}
        self.hits: list[str] = []

    def client(self) -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.headers["authorization"] == f"Bearer {TOKEN}"
            path = req.url.path.removeprefix("/espace/affichage/")
            self.hits.append(path)
            if path == "api/v1/agent/cache/manifest":
                return httpx.Response(200, json=self.manifest)
            if path in self.blobs:
                return httpx.Response(200, content=self.blobs[path])
            return httpx.Response(404)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_single_download_for_many_screens_and_integrity(tmp_path: Path) -> None:
    ma, mb, mc = "a" * 32, "b" * 32, "c" * 32
    origin = Origin(
        {
            f"media/{ma}/original": A,
            f"media/{mb}/original": A,  # même contenu, autre média : un seul téléchargement
            f"media/{mc}/original": b"corrompu",
        }
    )
    cache = MediaCache(tmp_path / "m", 10**9)
    manifest = {
        "items": [entry(A, ma), entry(A, mb), entry(B, mc)]  # B annoncé mais octets servis faux
    }
    res = await cache.sync(origin.client(), BASE, TOKEN, manifest)
    assert (res.items, res.bytes, res.missing) == (1, len(A), 1)
    assert sum(h.startswith("media/") for h in origin.hits) == 2  # A une fois, B une fois (échec)
    assert cache.has(sha(A)) and not cache.has(sha(B))
    assert not list((tmp_path / "m").glob(".*.part"))
    # deuxième passage : A déjà là, aucun nouveau téléchargement de A
    origin.hits.clear()
    await cache.sync(origin.client(), BASE, TOKEN, {"items": [entry(A, ma)]})
    assert origin.hits == []


async def test_rejects_unsafe_entries(tmp_path: Path) -> None:
    origin = Origin({})
    cache = MediaCache(tmp_path / "m", 10**9)
    bad = [
        {**entry(A), "sha256": "../../etc/passwd"},
        {**entry(A, "d" * 32), "path": "../secret"},
        {**entry(B), "path": "https://evil.test/x"},
    ]
    res = await cache.sync(origin.client(), BASE, TOKEN, {"items": bad})
    assert res.missing == 3 and origin.hits == []
    assert list((tmp_path / "m").iterdir()) == []


async def test_oversize_body_refused(tmp_path: Path) -> None:
    origin = Origin({f"media/{'a' * 32}/original": A + b"x"})
    cache = MediaCache(tmp_path / "m", 10**9)
    res = await cache.sync(origin.client(), BASE, TOKEN, {"items": [entry(A)]})
    assert res.missing == 1 and not cache.has(sha(A))


async def test_lru_eviction_outside_manifest(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path / "m", max_bytes=len(A) + len(B))
    old, mid = b"o" * 4000, b"m" * 4000
    for i, blob in enumerate((old, mid, A, B)):
        p = cache.path(sha(blob))
        assert p
        p.write_bytes(blob)
        import os

        os.utime(p, (1000 + i, 1000 + i))
    removed = cache.evict(keep={sha(A), sha(B)})
    assert removed == 2 and not cache.has(sha(old)) and not cache.has(sha(mid))
    # le manifeste n'est jamais évincé, même au-delà du quota
    small = MediaCache(tmp_path / "m", max_bytes=1)
    assert small.evict(keep={sha(A), sha(B)}) == 0 and small.has(sha(A))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def http(port: int, request: str) -> tuple[int, dict[str, str], bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(request.encode())
    await w.drain()
    raw = await asyncio.wait_for(r.read(), 5)
    w.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    hdrs = {k.lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
    return int(lines[0].split()[1]), hdrs, body


def get(path: str, extra: str = "", method: str = "GET") -> str:
    return f"{method} {path} HTTP/1.1\r\nHost: x\r\n{extra}\r\n"


async def test_lan_signed_urls(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path / "m", 10**9)
    cache.path(sha(A)).write_bytes(A)  # type: ignore[union-attr]
    key = b"k" * 32
    now = [1_000_000.0]
    keys: list[tuple[str, bytes] | None] = [("kid1", key)]
    port = free_port()
    srv = LanServer(cache, f"127.0.0.1:{port}", lambda: keys[0], clock=lambda: now[0])
    await srv.start()
    try:
        exp = int(now[0]) + 3600
        ok = signed_path(key, sha(A), exp, "kid1")
        st, h, body = await http(port, get(ok))
        assert (st, body) == (200, A) and h["accept-ranges"] == "bytes"
        st, h, body = await http(port, get(ok, "Range: bytes=5-9\r\n"))
        assert (st, body, h["content-range"]) == (206, A[5:10], f"bytes 5-9/{len(A)}")
        st, h, body = await http(port, get(ok, method="HEAD"))
        assert (st, body, h["content-length"]) == (200, b"", str(len(A)))
        assert (await http(port, get(ok, "Range: bytes=99999-\r\n")))[0] == 416
        # expirée, forgée, mauvaise empreinte, mauvais kid, clé absente
        assert (await http(port, get(signed_path(key, sha(A), int(now[0]) - 1, "kid1"))))[0] == 403
        forged = signed_path(b"z" * 32, sha(A), exp, "kid1")
        assert (await http(port, get(forged)))[0] == 403
        assert (await http(port, get(ok.replace("sig=", "sig=00"))))[0] == 403
        assert (await http(port, get(signed_path(key, sha(A), exp, "kid2"))))[0] == 403
        far = signed_path(key, sha(A), int(now[0]) + 10 * 86400, "kid1")
        assert (await http(port, get(far)))[0] == 403
        other = signed_path(key, sha(B), exp, "kid1")
        assert (await http(port, get(other)))[0] == 404  # signé mais absent du cache
        sig_of_a_on_b = ok.replace(sha(A), sha(B))
        assert (await http(port, get(sig_of_a_on_b)))[0] == 403  # signature liée à l'empreinte
        # aucune autre route, aucun listing, aucune écriture
        for bad in ("/", "/m/", "/m/../x", "/media/x", f"/m/{sha(A)}/extra"):
            assert (await http(port, get(bad)))[0] == 404
        assert (await http(port, get(ok, method="POST")))[0] == 405
        keys[0] = None
        assert (await http(port, get(ok)))[0] == 403
    finally:
        await srv.stop()


def test_parse_range() -> None:
    assert parse_range(None, 10) is None
    assert parse_range("bytes=2-4", 10) == (2, 4)
    assert parse_range("bytes=-3", 10) == (7, 9)
    assert parse_range("bytes=8-", 10) == (8, 9)
    assert parse_range("bytes=0-1,3-4", 10) == "invalid"
    assert parse_range("bytes=x-y", 10) == "invalid"


async def test_lan_disabled_by_default_and_opt_in(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, []) as srv:
        s = AgentSettings(server_url=srv.url, token=TOKEN, data_dir=tmp_path / "d1")
        a = Agent(s, Store(s.data_dir), http=httpx.AsyncClient())
        await a.start()
        assert s.cache_lan_listen is None and a.lan is None
        await a.close()
        port = free_port()
        s2 = AgentSettings(
            server_url=srv.url,
            token=TOKEN,
            data_dir=tmp_path / "d2",
            cache_lan_listen=f"127.0.0.1:{port}",
        )
        b = Agent(s2, Store(s2.data_dir), http=httpx.AsyncClient())
        await b.start()
        assert b.lan is not None
        await b.close()
    assert (
        AgentSettings.from_env(
            {"VDS_SERVER_URL": "https://h/", "VDS_AGENT_TOKEN": TOKEN}
        ).cache_lan_listen
        is None
    )


async def test_cache_sync_command_event_and_key(tmp_path: Path) -> None:
    ma = "a" * 32
    origin = Origin({f"media/{ma}/original": A})
    origin.manifest = {"items": [entry(A, ma)]}
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        srv.cache = {"enabled": True, "key_id": "kid1", "key": (b"k" * 32).hex()}
        srv.to_send = [cmd_frame(1, "cache.sync", {"revision": "m1"})]
        agent = make_agent(tmp_path, srv, plan())
        agent._http = origin.client()  # noqa: SLF001
        task = asyncio.create_task(agent.run())
        try:
            await eventually(lambda: srv.results() and srv.events())
            await eventually(
                lambda: any(
                    f.type == "heartbeat" and f.data["cache"]["items"] == 1 for f in srv.received
                )
            )
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)
        res = srv.results()[0]
        assert res["status"] == "ok" and res["data"] == {"items": 1, "bytes": len(A), "missing": 0}
        ev = srv.events()[0]
        assert ev["name"] == "cache.synced"
        assert ev["payload"]["items"] == 1 and ev["payload"]["site_id"]
        assert agent.cache.has(sha(A))
    # clé HMAC : stockée chiffrée, jamais en clair sur disque
    raw = (tmp_path / "data" / "agent.db").read_bytes()
    assert (b"k" * 32).hex().encode() not in raw
    assert await agent.store.state_get("agent.cache", "lan_key") == {
        "kid": "kid1",
        "key": (b"k" * 32).hex(),
    }


async def test_cache_sync_failures(tmp_path: Path) -> None:
    ma = "a" * 32
    origin = Origin({})  # média introuvable (404)
    origin.manifest = {"items": [entry(A, ma)]}
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        srv.to_send = [cmd_frame(1, "cache.sync", {"revision": "m1"})]
        agent = make_agent(tmp_path, srv, plan())
        agent._http = origin.client()  # noqa: SLF001
        task = asyncio.create_task(agent.run())
        try:
            await eventually(lambda: srv.results())
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)
        r = srv.results()[0]
        assert (r["status"], r["code"], r["data"]["missing"]) == (
            "failed",
            "agent.cache_incomplete",
            1,
        )
    assert Cascade and fast and sys and time  # imports partagés


def test_lan_peer_ipv4_mapped_and_public() -> None:
    from vds_agent.lan import is_lan_peer

    assert is_lan_peer("192.168.1.5")
    assert is_lan_peer("::ffff:192.168.1.5")
    assert not is_lan_peer("::ffff:8.8.8.8")
    assert not is_lan_peer("8.8.8.8")


def test_reserved_module_id_refused(tmp_path: Path) -> None:
    import json

    from vds_agent.modules import discover

    mod = tmp_path / "agent.evil"
    mod.mkdir()
    base = json.loads(
        (Path(__file__).resolve().parents[3] / "modules/power.adb/module.json").read_text()
    )
    (mod / "module.json").write_text(json.dumps(base | {"id": "agent.evil"}))
    assert discover(tmp_path) == []
