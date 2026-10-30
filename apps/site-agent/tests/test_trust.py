"""Serveur prouvé avant tout envoi de secret sur la boucle locale (revue I1)."""

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from test_enroll import CODE, FakeAgent, drop, until
from vds_agent.settings import AgentSettings
from vds_agent.store import Store
from vds_agent.supervisor import Supervisor
from vds_agent.trust import (
    ENROLL_CONTEXT,
    PROOF_CONTEXT,
    PROOF_PATH,
    ServerGuard,
    ServerUnverified,
    enroll_auth,
    new_http,
    read_key,
    required,
)

KEY = bytes(range(32))
BASE = "/espace/affichage"


def cfg(tmp: Path, url: str = f"http://127.0.0.1:8000{BASE}", **kw: Any) -> AgentSettings:
    (tmp / "local").mkdir(exist_ok=True)
    return AgentSettings(server_url=url, data_dir=tmp / "data", local_dir=tmp / "local", **kw)


def put_key(tmp: Path, key: bytes = KEY) -> None:
    (tmp / "local").mkdir(exist_ok=True)
    p = tmp / "local" / "proof.key"
    p.write_text(key.hex())
    p.chmod(0o600)


def server(key: bytes | None = KEY, calls: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        assert req.url.path == BASE + PROOF_PATH
        if key is None:
            return httpx.Response(200, json={"proof": "00"})
        nonce = req.url.params["nonce"]
        proof = hmac.new(key, PROOF_CONTEXT + nonce.encode(), hashlib.sha256).hexdigest()
        return httpx.Response(200, json={"proof": proof})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_required_only_for_local_cleartext(tmp_path: Path) -> None:
    assert required(cfg(tmp_path))
    assert not required(cfg(tmp_path, "https://vds.example/espace/affichage"))
    assert not required(AgentSettings(server_url="https://x.example"))  # agent distant


def test_read_key_rejects_bad_files(tmp_path: Path) -> None:
    (tmp_path / "local").mkdir()
    d = tmp_path / "local"
    assert read_key(d) is None
    (d / "proof.key").write_text("zz")
    assert read_key(d) is None
    (d / "proof.key").write_text("ab" * 8)  # trop courte
    assert read_key(d) is None
    (d / "proof.key").unlink()
    (d / "other").write_text(KEY.hex())
    os.symlink(d / "other", d / "proof.key")
    assert read_key(d) is None  # lien symbolique refusé
    (d / "proof.key").unlink()
    put_key(tmp_path)
    assert read_key(d) == KEY


async def test_valid_proof_passes_and_is_cached_briefly(tmp_path: Path) -> None:
    put_key(tmp_path)
    calls: list[httpx.Request] = []
    now = [0.0]
    g = ServerGuard(cfg(tmp_path), server(calls=calls), clock=lambda: now[0])
    await g.verify()
    await g.verify()
    assert len(calls) == 1  # TTL
    await g.verify(fresh=True)
    assert len(calls) == 2
    now[0] = 6.0
    await g.verify()
    assert len(calls) == 3
    assert len({c.url.params["nonce"] for c in calls}) == 3  # nonce frais à chaque défi


@pytest.mark.parametrize("impostor", ["wrong_key", "no_key_file", "down", "garbage", "status"])
async def test_impostor_is_refused(tmp_path: Path, impostor: str) -> None:
    if impostor != "no_key_file":
        put_key(tmp_path)

    def handler(req: httpx.Request) -> httpx.Response:
        if impostor == "down":
            raise httpx.ConnectError("refused")
        if impostor == "garbage":
            return httpx.Response(200, text="<html>")
        if impostor == "status":
            return httpx.Response(404)
        return httpx.Response(200, json={"proof": "0" * 64})

    client = (
        server(KEY)
        if impostor == "no_key_file"
        else httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ServerUnverified):
        await ServerGuard(cfg(tmp_path), client).verify()


async def test_proof_is_not_reusable_for_another_nonce(tmp_path: Path) -> None:
    put_key(tmp_path)
    stale = hmac.new(KEY, PROOF_CONTEXT + b"autre", hashlib.sha256).hexdigest()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"proof": stale}))
    )
    with pytest.raises(ServerUnverified):
        await ServerGuard(cfg(tmp_path), client).verify()


async def test_hook_blocks_secret_requests_to_unproven_server(tmp_path: Path) -> None:
    """Aucune requête (ni jeton Bearer) ne part vers un serveur qui n'a pas prouvé."""
    put_key(tmp_path)
    s = cfg(tmp_path)
    sent: list[httpx.Request] = []
    http, guard = new_http(s)
    guard._client = httpx.AsyncClient(  # serveur imposteur
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"proof": "0" * 64}))
    )
    http._transport = httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))  # type: ignore[arg-type,func-returns-value]
    with pytest.raises(ServerUnverified):
        await http.get(f"{s.http_base}/api/v1/agent/plan", headers={"Authorization": "Bearer t"})
    assert sent == []
    guard._client = server()  # le vrai serveur
    r = await http.get(f"{s.http_base}/api/v1/agent/plan", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and len(sent) == 1
    await http.aclose()
    await guard.aclose()


async def test_remote_agent_is_untouched(tmp_path: Path) -> None:
    g = ServerGuard(AgentSettings(server_url="https://vds.example"), server(None))
    await g.verify()  # ne fait rien, ne lève pas


def test_enroll_auth_matches_server_formula(tmp_path: Path) -> None:
    (tmp_path / "local").mkdir()
    assert enroll_auth(tmp_path / "local", CODE) is None
    put_key(tmp_path)
    want = hmac.new(KEY, ENROLL_CONTEXT + CODE.encode(), hashlib.sha256).hexdigest()
    assert enroll_auth(tmp_path / "local", CODE) == want


async def test_handoff_stays_in_place_while_server_is_unproven(tmp_path: Path) -> None:
    """Code non consommé (fichier conservé) tant que le serveur n'est pas prouvé."""
    put_key(tmp_path)
    s = cfg(tmp_path)
    posts: list[httpx.Request] = []

    def api(req: httpx.Request) -> httpx.Response:
        posts.append(req)
        return httpx.Response(500)

    bad = ServerGuard(
        s,
        httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"proof": "0" * 64}))
        ),
    )
    sv = Supervisor(
        s,
        Store(s.data_dir),
        agent_factory=FakeAgent,  # type: ignore[arg-type]
        http=httpx.AsyncClient(transport=httpx.MockTransport(api)),
        guard=bad,
        sleep=lambda _t: asyncio.sleep(0),
        watch_s=0.01,
        presence_s=0.01,
    )
    task = asyncio.create_task(sv.run())
    await until(lambda: (tmp_path / "local" / "presence.json").exists())
    drop(tmp_path / "local")
    await asyncio.sleep(0.3)
    assert (tmp_path / "local" / "enroll.json").exists() and posts == []
    assert json.loads((tmp_path / "local" / "enroll.json").read_text())["code"] == CODE
    sv.stop()
    await task


async def test_exchange_sends_local_auth_header_and_never_without_proof() -> None:
    from vds_agent.enroll import EnrollError, exchange

    seen: list[str | None] = []

    def ok(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("x-vds-local-auth"))
        return httpx.Response(401)

    async def nosleep(_t: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    with pytest.raises(EnrollError):
        await exchange(http, "http://x", CODE, local_auth="abc", sleep=nosleep)
    with pytest.raises(EnrollError):
        await exchange(http, "http://x", CODE, sleep=nosleep)
    assert seen == ["abc", None]

    class Refusing(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise ServerUnverified("x")

    with pytest.raises(EnrollError) as e:
        await exchange(httpx.AsyncClient(transport=Refusing()), "http://x", CODE, sleep=nosleep)
    assert e.value.code == "enroll.server_unverified"
