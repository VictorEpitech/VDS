"""Enrôlement et agent local : faux serveur HTTP (MockTransport), faux Agent, dossier temporaire."""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from vds_agent.enroll import (
    PRESENCE_FILE,
    EnrollError,
    exchange,
    local_hostname,
    take_handoff,
)
from vds_agent.settings import AgentSettings, SettingsError, http_base, ws_url
from vds_agent.store import Store
from vds_agent.supervisor import CredentialsMissingError, Supervisor
from vds_sdk.agent.enroll import parse_presence

CODE = "vds_enr_" + "0123456789ABCDEFGHJKMNPQRS"
CODE2 = "vds_enr_" + "SRQPNMKJHGFEDCBA9876543210"
TOK1, TOK2 = "vds_agt_" + "a" * 20, "vds_agt_" + "b" * 20


async def nosleep(_: float) -> None:
    await asyncio.sleep(0)


class FakeEnrollServer:
    def __init__(self, tokens: list[str] | None = None, status: int = 200) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tokens = list(tokens or [TOK1, TOK2])
        self.status = status
        self.statuses: list[int] = []

    def client(self) -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/espace/affichage/api/v1/agent/enroll"
            assert "origin" not in req.headers and "cookie" not in req.headers
            self.calls.append(json.loads(req.content))
            st = self.statuses.pop(0) if self.statuses else self.status
            if st != 200:
                return httpx.Response(st)
            return httpx.Response(
                200,
                json={
                    "agent_id": str(uuid4()),
                    "site_id": str(uuid4()),
                    "token": self.tokens.pop(0),
                    "ws_path": "ws/agent",
                },
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class OkGuard:
    """Serveur toujours prouvé (la preuve a ses propres tests : test_trust.py)."""

    def __init__(self, _s: AgentSettings) -> None:
        pass

    async def verify(self, *, fresh: bool = False) -> None:
        return None

    async def aclose(self) -> None:
        return None


class FakeAgent:
    """Remplace Agent : tourne jusqu'à stop() ou fin forcée (revocation)."""

    instances: list["FakeAgent"] = []

    def __init__(self, settings: AgentSettings, store: Store) -> None:
        self.token = settings.token
        self.end_reason: str | None = None
        self._stop = asyncio.Event()
        FakeAgent.instances.append(self)

    def stop(self) -> None:
        self._stop.set()

    def revoke(self) -> None:
        self.end_reason = "revoked"
        self._stop.set()

    async def run(self) -> None:
        await self._stop.wait()


@pytest.fixture(autouse=True)
def _reset() -> None:
    FakeAgent.instances.clear()


def settings(tmp: Path, **kw: Any) -> AgentSettings:
    base: dict[str, Any] = {
        "server_url": "http://127.0.0.1:8000/espace/affichage",
        "data_dir": tmp / "data",
        "local_dir": tmp / "local",
    }
    (tmp / "local").mkdir(exist_ok=True)
    base.update(kw)
    return AgentSettings(**base)


def sup(s: AgentSettings, srv: FakeEnrollServer) -> tuple[Supervisor, Store]:
    store = Store(s.data_dir)
    return (
        Supervisor(
            s,
            store,
            agent_factory=FakeAgent,  # type: ignore[arg-type]
            http=srv.client(),
            guard=OkGuard(s),  # type: ignore[arg-type]
            sleep=nosleep,
            watch_s=0.01,
            presence_s=0.01,
        ),
        store,
    )


def drop(directory: Path, code: str = CODE) -> None:
    doc = {
        "v": 1,
        "code": code,
        "enrollment_id": str(uuid4()),
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }
    tmp = directory / ".x.tmp"
    tmp.write_text(json.dumps(doc))
    tmp.chmod(0o600)
    os.replace(tmp, directory / "enroll.json")


async def until(cond: Any, t: float = 3.0) -> None:
    async def loop() -> None:
        while not cond():  # noqa: ASYNC110 - attente de test
            await asyncio.sleep(0.01)

    await asyncio.wait_for(loop(), t)


def presence(directory: Path) -> Any:
    return parse_presence((directory / PRESENCE_FILE).read_bytes())


# --- URL : clair vers loopback seul ------------------------------------------------


def test_cleartext_loopback_only() -> None:
    assert ws_url("http://127.0.0.1:8000") == "ws://127.0.0.1:8000/ws/agent"
    assert http_base("http://127.0.0.1:8000/espace/affichage/") == (
        "http://127.0.0.1:8000/espace/affichage"
    )
    with pytest.raises(SettingsError):
        ws_url("http://192.168.1.10:8000")
    with pytest.raises(SettingsError):
        http_base("http://localhost:8000")
    assert ws_url("http://192.168.1.10:8000", True).startswith("ws://192.168.1.10")


def test_local_dir_derives_loopback_url() -> None:
    s = AgentSettings.from_env(
        {"VDS_AGENT_LOCAL_DIR": "/x", "VDS_PORT": "8002", "VDS_BASE_PATH": "/espace/affichage"}
    )
    assert s.server_url == "http://127.0.0.1:8002/espace/affichage"
    assert AgentSettings.from_env({"VDS_AGENT_LOCAL_DIR": "/x"}).server_url == (
        "http://127.0.0.1:8000"
    )
    with pytest.raises(SettingsError):
        AgentSettings.from_env({})


# --- échange ---------------------------------------------------------------------------


async def test_exchange_errors_and_retries() -> None:
    srv = FakeEnrollServer()
    base = "http://x/espace/affichage"
    for st, code in [(401, "enroll.invalid"), (410, "enroll.expired"), (409, "enroll.consumed")]:
        srv.status = st
        srv.calls.clear()
        with pytest.raises(EnrollError) as e:
            await exchange(srv.client(), base, CODE, sleep=nosleep)
        assert e.value.code == code and len(srv.calls) == 1  # abandon immédiat
    srv.status, srv.calls = 429, []
    with pytest.raises(EnrollError) as e:
        await exchange(srv.client(), base, CODE, sleep=nosleep)
    assert e.value.code == "enroll.rate_limited" and len(srv.calls) == 1
    srv.status, srv.calls = 503, []
    with pytest.raises(EnrollError) as e:
        await exchange(srv.client(), base, CODE, sleep=nosleep)
    assert e.value.code == "enroll.unreachable" and len(srv.calls) == 3
    srv.statuses, srv.calls = [503, 200], []
    srv.status = 200
    r = await exchange(srv.client(), base, CODE, TOK1, sleep=nosleep)
    assert r.token.get_secret_value() == TOK1 and len(srv.calls) == 2
    assert srv.calls[0]["previous_token"] == TOK1


def test_handoff_read_deletes_file_and_rejects_bad(tmp_path: Path) -> None:
    drop(tmp_path)
    h = take_handoff(tmp_path)
    assert not isinstance(h, EnrollError | type(None)) and h.code.get_secret_value() == CODE
    assert not (tmp_path / "enroll.json").exists()
    assert take_handoff(tmp_path) is None
    (tmp_path / "enroll.json").write_text("{pas du json")
    assert isinstance(take_handoff(tmp_path), EnrollError)
    assert not (tmp_path / "enroll.json").exists()
    (tmp_path / "secret").write_text("x")
    os.symlink(tmp_path / "secret", tmp_path / "enroll.json")  # lien symbolique refusé
    assert isinstance(take_handoff(tmp_path), EnrollError)
    assert (tmp_path / "secret").exists()


# --- ordre de démarrage ----------------------------------------------------------------


async def test_no_credentials_exit_2(tmp_path: Path) -> None:
    s = settings(tmp_path, local_dir=None)
    sv, _ = sup(s, FakeEnrollServer())
    with pytest.raises(CredentialsMissingError):
        await sv.run()


async def test_env_token_has_priority_and_ignores_local(tmp_path: Path) -> None:
    srv = FakeEnrollServer()
    s = settings(tmp_path, token=TOK1, enroll_code=CODE)
    sv, _ = sup(s, srv)
    task = asyncio.create_task(sv.run())
    await until(lambda: FakeAgent.instances)
    drop(tmp_path / "local")
    await asyncio.sleep(0.1)
    assert FakeAgent.instances[0].token == TOK1 and not srv.calls
    assert (tmp_path / "local" / "enroll.json").exists()  # canal local ignoré
    sv.stop()
    FakeAgent.instances[0].stop()
    await task


async def test_env_code_exchanged_stored_encrypted_then_reused_offline(tmp_path: Path) -> None:
    srv = FakeEnrollServer()
    s = settings(tmp_path, local_dir=None, enroll_code=CODE)
    sv, store = sup(s, srv)
    task = asyncio.create_task(sv.run())
    await until(lambda: FakeAgent.instances)
    assert FakeAgent.instances[0].token == TOK1 and len(srv.calls) == 1
    assert await store.state_get("core", "agent_token") == TOK1
    assert TOK1.encode() not in (tmp_path / "data" / "agent.db").read_bytes()  # chiffré
    FakeAgent.instances[0].stop()
    await task
    # redémarrage hors ligne (serveur d'enrôlement injoignable) : jeton rangé, code ignoré
    srv2 = FakeEnrollServer(status=503)
    sv2, _ = sup(s, srv2)
    task = asyncio.create_task(sv2.run())
    await until(lambda: len(FakeAgent.instances) == 2)
    assert FakeAgent.instances[1].token == TOK1 and not srv2.calls
    FakeAgent.instances[1].stop()
    await task


async def test_env_code_failure_without_local_exits_2(tmp_path: Path) -> None:
    s = settings(tmp_path, local_dir=None, enroll_code=CODE)
    sv, _ = sup(s, FakeEnrollServer(status=410))
    assert await sv.run() == 2


# --- canal local -----------------------------------------------------------------------


async def test_dormant_then_handoff_then_revocation_back_to_dormant(tmp_path: Path) -> None:
    srv = FakeEnrollServer()
    s = settings(tmp_path)
    local = tmp_path / "local"
    sv, store = sup(s, srv)
    task = asyncio.create_task(sv.run())
    await until(lambda: (local / PRESENCE_FILE).exists())
    assert presence(local).state == "dormant"
    await asyncio.sleep(0.1)
    assert not FakeAgent.instances and not srv.calls  # sommeil : aucune connexion
    drop(local)
    await until(lambda: FakeAgent.instances)
    assert not (local / "enroll.json").exists() and len(srv.calls) == 1
    assert FakeAgent.instances[0].token == TOK1
    await until(lambda: presence(local).state == "active")
    assert presence(local).agent_id is not None
    assert "vds_enr_" not in (local / PRESENCE_FILE).read_text()
    # révocation : jeton effacé, retour au sommeil
    FakeAgent.instances[0].revoke()
    await until(lambda: presence(local).state == "dormant")
    assert await store.state_get("core", "agent_token") is None
    assert presence(local).agent_id is None
    # nouveau dépôt : réenrôlement sans previous_token (effacé)
    drop(local, CODE2)
    await until(lambda: len(FakeAgent.instances) == 2)
    assert FakeAgent.instances[1].token == TOK2
    assert srv.calls[1]["previous_token"] is None
    sv.stop()
    FakeAgent.instances[1].stop()
    await task


async def test_handoff_deleted_before_network_call(tmp_path: Path) -> None:
    local = tmp_path / "local"
    seen: list[bool] = []
    srv = FakeEnrollServer()

    def client() -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append((local / "enroll.json").exists())
            return httpx.Response(401)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    srv.client = client  # type: ignore[method-assign]
    s = settings(tmp_path)
    sv, _ = sup(s, srv)
    task = asyncio.create_task(sv.run())
    await until(lambda: (local / PRESENCE_FILE).exists())
    drop(local)
    await until(lambda: seen)
    assert seen == [False]
    await until(lambda: presence(local).state == "error")
    assert presence(local).error == "enroll.invalid"
    assert not FakeAgent.instances  # reste en sommeil, code non rejoué
    sv.stop()
    await task


async def test_active_agent_reenrolls_with_previous_token(tmp_path: Path) -> None:
    srv = FakeEnrollServer()
    s = settings(tmp_path)
    local = tmp_path / "local"
    sv, store = sup(s, srv)
    task = asyncio.create_task(sv.run())
    await until(lambda: (local / PRESENCE_FILE).exists())
    drop(local)
    await until(lambda: FakeAgent.instances)
    drop(local, CODE2)  # « déplacer l'agent local »
    await until(lambda: len(FakeAgent.instances) == 2)
    assert srv.calls[1]["previous_token"] == TOK1
    assert FakeAgent.instances[1].token == TOK2
    assert await store.state_get("core", "agent_token") == TOK2
    sv.stop()
    FakeAgent.instances[1].stop()
    await task


async def test_failed_move_keeps_running_agent(tmp_path: Path) -> None:
    srv = FakeEnrollServer()
    s = settings(tmp_path)
    local = tmp_path / "local"
    sv, _ = sup(s, srv)
    task = asyncio.create_task(sv.run())
    await until(lambda: (local / PRESENCE_FILE).exists())
    drop(local)
    await until(lambda: FakeAgent.instances)
    srv.status = 409
    drop(local, CODE2)
    await until(lambda: presence(local).error == "enroll.consumed")
    assert presence(local).state == "active" and len(FakeAgent.instances) == 1
    sv.stop()
    FakeAgent.instances[0].stop()
    await task


def test_hostname_matches_contract() -> None:
    import re

    assert re.fullmatch(r"[A-Za-z0-9._-]+", local_hostname())


def test_websockets_debug_never_logs_bearer_token() -> None:
    """`websockets` journalise l'en-tête Authorization en DEBUG : plafonné à INFO même si DEBUG."""
    import logging

    from vds_agent.logsetup import setup_logging

    setup_logging(logging.DEBUG)
    try:
        assert not logging.getLogger("websockets.client").isEnabledFor(logging.DEBUG)
    finally:
        setup_logging()
