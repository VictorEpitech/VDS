# ruff: noqa: F811, ASYNC240
"""Durcissement de l'enrôlement (revue I1, I2, I3, I5) : preuve du serveur, seaux, rafales."""

import asyncio
import hashlib
import hmac
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_enrollment import HOST, _enrollment_clean, issue, mk_site  # noqa: F401
from vds_sdk.agent.enroll import ENROLL_CODE_PREFIX
from vds_server.core.errors import ApiError
from vds_server.enrollment.local import ENROLL_CONTEXT, KEY_FILE, PROOF_CONTEXT, LocalChannel
from vds_server.player.ratelimit import FailureLimiter

BAD = ENROLL_CODE_PREFIX + "1" * 26
NONCE = "ab" * 16


@pytest.fixture
def local_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "local-agent"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("VDS_LOCAL_AGENT_DIR", str(d))
    yield d


def auth(key: bytes, code: str) -> str:
    return hmac.new(key, ENROLL_CONTEXT + code.encode(), hashlib.sha256).hexdigest()


# --- limiteur : réservation atomique (I3) ---------------------------------------------------


def test_reserve_counts_before_work_and_burst_cannot_exceed() -> None:
    lim = FailureLimiter(limit=3, window_s=60, clock=lambda: 0.0)
    for _ in range(3):
        lim.reserve("k")
    with pytest.raises(ApiError) as e:
        lim.reserve("k")  # rien n'a été rendu : la rafale est bornée dès la réservation
    assert e.value.status == 429
    lim.reserve("autre")  # autres clés intactes


def test_release_gives_the_slot_back_only_for_that_attempt() -> None:
    lim = FailureLimiter(limit=2, window_s=60, clock=lambda: 0.0)
    a = lim.reserve("k")
    lim.reserve("k")
    lim.release(a)
    lim.reserve("k")  # une place rendue
    with pytest.raises(ApiError):
        lim.reserve("k")


def test_window_expires_and_purge_is_bounded() -> None:
    now = [0.0]
    lim = FailureLimiter(limit=1, window_s=10, clock=lambda: now[0])
    lim.reserve("k")
    now[0] = 11.0
    lim.reserve("k")
    for i in range(1_500):
        lim.reserve(f"ip{i}")
    now[0] = 100.0
    lim.reserve("nouveau")  # déclenche la purge (au plus une par minute)
    assert len(lim._fails) < 10


async def test_concurrent_burst_is_capped_before_database(
    admin: AsyncClient, p: str, app: FastAPI
) -> None:
    async def one() -> int:
        async with new_client(app) as c:
            r = await c.post(f"{p}/api/v1/agent/enroll", json={"code": BAD, **HOST})
            return r.status_code

    codes = await asyncio.gather(*(one() for _ in range(60)))
    assert set(codes) <= {401, 429}
    assert codes.count(401) <= 10  # jamais plus de `limit` passages au travail DB
    assert codes.count(429) >= 50


async def test_gate_refuses_without_touching_database(
    admin: AsyncClient, p: str, app: FastAPI
) -> None:
    app.state.runtime.enroll_gate = asyncio.Semaphore(0)  # pleine
    async with new_client(app) as c:
        r = await c.post(f"{p}/api/v1/agent/enroll", json={"code": BAD, **HOST})
    assert r.status_code == 429 and r.json()["code"] == "errors.rate_limited"


async def test_non_counted_errors_release_the_slot(app: FastAPI, p: str) -> None:
    async with new_client(app) as c:
        for _ in range(15):  # 422 : jamais compté
            r = await c.post(f"{p}/api/v1/agent/enroll", json={"code": "court", **HOST})
            assert r.status_code == 422
        r = await c.post(f"{p}/api/v1/agent/enroll", json={"code": BAD, **HOST})
    assert r.status_code == 401


# --- preuve du serveur (I1) ------------------------------------------------------------------


async def test_proof_endpoint_signs_nonce_with_volume_secret(
    local_dir: Path, app: FastAPI, p: str
) -> None:
    async with new_client(app) as c:
        r = await c.get(f"{p}/api/v1/agent/local/proof", params={"nonce": NONCE})
        assert r.status_code == 200
        key_path = local_dir / KEY_FILE
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
        key = bytes.fromhex(key_path.read_text())
        assert len(key) == 32
        want = hmac.new(key, PROOF_CONTEXT + NONCE.encode(), hashlib.sha256).hexdigest()
        assert r.json() == {"proof": want}
        assert key.hex() not in r.text
        # une clé existante n'est jamais écrasée (autre worker, redémarrage)
        r2 = await c.get(f"{p}/api/v1/agent/local/proof", params={"nonce": "cd" * 16})
        assert bytes.fromhex(key_path.read_text()) == key and r2.status_code == 200
    assert not list(local_dir.glob(".proof.key.*"))


async def test_proof_endpoint_inputs(local_dir: Path, app: FastAPI, p: str) -> None:
    async with new_client(app) as c:
        url = f"{p}/api/v1/agent/local/proof"
        assert (await c.get(url, params={"nonce": "court"})).status_code == 422
        assert (await c.get(url, params={"nonce": "Z" * 32})).status_code == 422
        assert (await c.get(url)).status_code == 422
        r = await c.get(url, params={"nonce": NONCE}, headers={"Origin": "https://vds.test"})
        assert r.status_code == 403


async def test_proof_unavailable_without_local_channel(app: FastAPI, p: str) -> None:
    async with new_client(app) as c:
        r = await c.get(f"{p}/api/v1/agent/local/proof", params={"nonce": NONCE})
    assert (r.status_code, r.json()["code"]) == (404, "agent.local.unavailable")


async def test_key_file_symlink_is_refused(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (tmp_path / "evil").write_text("ab" * 32)
    os.symlink(tmp_path / "evil", d / KEY_FILE)
    with pytest.raises(OSError):
        await LocalChannel(d).key()


async def test_two_channels_converge_on_same_key(tmp_path: Path) -> None:
    a, b = LocalChannel(tmp_path), LocalChannel(tmp_path)
    ka, kb = await asyncio.gather(a.key(), b.key())
    assert ka == kb and len(ka or b"") == 32


# --- seaux distincts (I2) ---------------------------------------------------------------------


async def test_local_agent_has_its_own_bucket_both_ways(
    local_dir: Path, admin: AsyncClient, p: str, app: FastAPI
) -> None:
    async with new_client(app) as c:
        proof = await c.get(f"{p}/api/v1/agent/local/proof", params={"nonce": NONCE})
        assert proof.status_code == 200
    key = bytes.fromhex((local_dir / KEY_FILE).read_text())
    site = await mk_site(admin, p)

    async def post(code: str, header: str | None = None) -> int:
        async with new_client(app) as c:
            h = {"X-VDS-Local-Auth": header} if header else {}
            r = await c.post(f"{p}/api/v1/agent/enroll", json={"code": code, **HOST}, headers=h)
            return r.status_code

    # un anonyme épuise SON seau (la passerelle d'un proxy hôte est la même IP pour tous)
    for _ in range(10):
        assert await post(BAD) == 401
    assert await post(BAD) == 429
    # l'agent local authentifié passe quand même
    out = await issue(admin, p, site)
    assert await post(out["code"], auth(key, out["code"])) == 200
    # et ses propres échecs n'affectent pas les autres : seau « local » épuisé à part
    for _ in range(10):
        assert await post(BAD, auth(key, BAD)) == 401
    assert await post(BAD, auth(key, BAD)) == 429


async def test_forged_local_header_gets_no_special_bucket(
    local_dir: Path, app: FastAPI, p: str
) -> None:
    async with new_client(app) as c:
        await c.get(f"{p}/api/v1/agent/local/proof", params={"nonce": NONCE})
        codes = []
        for _ in range(12):
            r = await c.post(
                f"{p}/api/v1/agent/enroll",
                json={"code": BAD, **HOST},
                headers={"X-VDS-Local-Auth": "0" * 64},
            )
            codes.append(r.status_code)
    assert codes == [401] * 10 + [429] * 2  # traité comme anonyme, pas exempté


# --- commande docker run durcie (I5) ----------------------------------------------------------


async def test_generated_docker_run_is_hardened(admin: AsyncClient, p: str) -> None:
    site = await mk_site(admin, p)
    cmd = (await issue(admin, p, site))["commands"]["docker_run"]
    for flag in (
        "--user 10001:10001",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
        "--read-only",
        "--tmpfs /tmp",
        "--memory 512m",
        "--pids-limit 256",
    ):
        assert flag in cmd
    assert cmd.index("--cap-drop") < cmd.index("VDS_ENROLL_CODE")


# --- réécriture d'IP par proxy de confiance seulement (I2) -------------------------------------


async def test_uvicorn_trusts_forwarded_for_only_from_allowed_peers() -> None:
    import httpx
    import uvicorn

    seen: list[str] = []

    async def asgi(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        seen.append(scope["client"][0])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run(allow: str, port: int) -> None:
        cfg = uvicorn.Config(
            asgi,
            host="127.0.0.1",
            port=port,
            proxy_headers=True,
            forwarded_allow_ips=allow,
            log_level="error",
        )
        srv = uvicorn.Server(cfg)
        task = asyncio.create_task(srv.serve())
        while not srv.started:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        async with httpx.AsyncClient() as c:
            await c.get(f"http://127.0.0.1:{port}/", headers={"X-Forwarded-For": "203.0.113.9"})
        srv.should_exit = True
        await task

    await run("127.0.0.1", 18761)  # le pair est le proxy de confiance : l'en-tête compte
    await run("10.9.9.9", 18762)  # pair non listé : en-tête ignoré, IP du pair
    assert seen == ["203.0.113.9", "127.0.0.1"]
