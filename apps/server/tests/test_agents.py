"""Agents de site : jetons, /ws/agent (faux client WS ASGI), commandes seq/ack/reprise, pont."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, p  # noqa: F401
from test_diffusion import mk_screen
from vds_sdk import Event
from vds_sdk.wire import SUBPROTOCOL_AGENT

HELLO = {
    "agent_version": "1.0.0",
    "core": "1.2.0",
    "modules": [],
    "last_server_seq": 0,
    "host": {"os": "linux", "arch": "amd64"},
}


class FakeWS:
    """Client WebSocket ASGI minimal : pilote l'application montée (donc le préfixe)."""

    def __init__(self, app: FastAPI, path: str, headers: dict[str, str], protos: list[str]) -> None:
        self._in: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._out: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        if protos:
            headers = {**headers, "Sec-WebSocket-Protocol": ", ".join(protos)}
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "subprotocols": protos,
            "client": ("127.0.0.1", 1),
            "server": ("vds.test", 80),
        }
        self._task = asyncio.create_task(app(scope, self._in.get, self._out.put))  # type: ignore[arg-type]
        self._in.put_nowait({"type": "websocket.connect"})
        self.accepted = False
        self.close_code: int | None = None
        self.subprotocol: str | None = None

    async def _next(self, timeout: float = 5) -> dict[str, Any]:  # noqa: ASYNC109
        return await asyncio.wait_for(self._out.get(), timeout)

    async def open(self) -> "FakeWS":
        msg = await self._next()
        if msg["type"] == "websocket.accept":
            self.accepted = True
            self.subprotocol = msg.get("subprotocol")
        else:
            self.close_code = msg.get("code")
        return self

    async def send(self, type_: str, data: dict[str, Any] | None = None, **kw: Any) -> None:
        frame = {"v": 1, "type": type_, "id": str(uuid4()), "ts": datetime.now(UTC).isoformat()}
        frame |= {"data": data or {}, **kw}
        self._in.put_nowait({"type": "websocket.receive", "text": json.dumps(frame)})

    async def recv(self) -> dict[str, Any] | None:
        """Trame suivante, ou None si fermée (close_code renseigné)."""
        msg = await self._next()
        if msg["type"] == "websocket.send":
            return dict(json.loads(msg["text"]))
        self.close_code = msg.get("code")
        return None

    async def hello(self, **over: Any) -> dict[str, Any]:
        await self.send("hello", HELLO | over)
        welcome = await self.recv()
        assert welcome is not None, self.close_code
        assert welcome["type"] == "welcome"
        return dict(welcome["data"])

    async def close(self) -> None:
        self._in.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self._task, 5)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def connect(
    app: FastAPI, p: str, token: str | None, *, protos: list[str] | None = None, **headers: str
) -> FakeWS:
    h = dict(headers)
    if token:
        h |= auth(token)
    ws = FakeWS(app, f"{p}/ws/agent", h, [SUBPROTOCOL_AGENT] if protos is None else protos)
    return await ws.open()


async def refused(
    app: FastAPI, p: str, token: str | None, *, protos: list[str] | None = None, **headers: str
) -> int | None:
    ws = await connect(app, p, token, protos=protos, **headers)
    if ws.accepted:
        assert await ws.recv() is None
    return ws.close_code


async def sql(url: str, query: str) -> list[Any]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        result = await c.execute(text(query))
        rows = list(result.all()) if result.returns_rows else []
    await engine.dispose()
    return rows


@pytest.fixture(autouse=True)
async def _queue_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    await sql(migrated_db_url, "TRUNCATE remote_command")


@pytest.fixture
async def setup(admin: AsyncClient, p: str) -> AsyncIterator[dict[str, Any]]:  # noqa: F811
    site, screen = await mk_screen(admin, p)
    r = await admin.post(f"{p}/api/v1/sites/{site}/agents", json={"name": "Moulins"}, headers=SAME)
    assert r.status_code == 201, r.text
    body = r.json()
    yield {"site": site, "screen": screen, "agent": body["agent"]["id"], "token": body["token"]}


def cmd_url(p: str, s: dict[str, Any]) -> str:
    return f"{p}/api/v1/sites/{s['site']}/agents/{s['agent']}/commands"


async def test_admin_lifecycle(
    admin: AsyncClient, p: str, setup: dict[str, Any], app: FastAPI, migrated_db_url: str
) -> None:  # noqa: F811
    s = setup
    assert s["token"].startswith("vds_agt_")
    rows = await sql(migrated_db_url, "SELECT token_hash, token_prefix FROM site_agent")
    assert rows[0][0] != s["token"] and len(rows[0][0]) == 64 and rows[0][1] == s["token"][:8]
    url = f"{p}/api/v1/sites/{s['site']}/agents"
    lst = (await admin.get(url)).json()
    assert lst[0]["status"] == "unseen" and "token" not in lst[0]
    # un seul agent actif par site
    r = await admin.post(url, json={"name": "bis"}, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "agents.already_active")
    # rotation : l'ancien jeton est refusé, la connexion ouverte est fermée en 4410
    ws = await connect(app, p, s["token"])
    await ws.hello()
    r = await admin.post(f"{url}/{s['agent']}/rotate", headers=SAME)
    new = r.json()["token"]
    assert new != s["token"]
    assert await ws.recv() is None and ws.close_code == 4410
    assert await refused(app, p, s["token"]) == 4401
    ws2 = await connect(app, p, new)
    await ws2.hello()
    assert (await admin.get(url)).json()[0]["status"] == "online"
    # révocation
    r = await admin.post(f"{url}/{s['agent']}/revoke", headers=SAME)
    assert r.json()["status"] == "revoked"
    assert await ws2.recv() is None and ws2.close_code == 4410
    assert await refused(app, p, new) == 4410
    r = await admin.post(f"{url}/{s['agent']}/rotate", headers=SAME)
    assert r.status_code == 409
    # nouvel agent possible après révocation
    assert (await admin.post(url, json={"name": "n"}, headers=SAME)).status_code == 201


async def test_admin_only(
    anon: AsyncClient,
    admin: AsyncClient,
    p: str,  # noqa: F811
    setup: dict[str, Any],
    app: FastAPI,
) -> None:
    from test_auth import new_client

    async with new_client(app) as other:
        r = await other.get(f"{p}/api/v1/sites/{setup['site']}/agents")
        assert r.status_code == 401


async def test_handshake_refusals(app: FastAPI, p: str, setup: dict[str, Any]) -> None:
    assert (await connect(app, p, setup["token"], Origin="https://evil.test")).accepted is False
    assert await refused(app, p, setup["token"], protos=["x"]) == 4426
    assert await refused(app, p, setup["token"], protos=[]) == 4426
    assert await refused(app, p, None) == 4401
    assert await refused(app, p, "vds_agt_inconnu") == 4401
    ws = await connect(app, p, setup["token"])
    await ws.send("heartbeat", {})  # pas de hello
    assert await ws.recv() is None and ws.close_code == 4400


async def test_hello_welcome_and_replace(app: FastAPI, p: str, setup: dict[str, Any]) -> None:
    ws = await connect(app, p, setup["token"])
    assert ws.subprotocol == SUBPROTOCOL_AGENT
    w = await ws.hello()
    assert w["agent_id"] == setup["agent"] and w["site_id"] == setup["site"]
    assert w["heartbeat_s"] == 30 and w["resume_from"] == 0
    ws2 = await connect(app, p, setup["token"])
    await ws2.hello()
    assert await ws.recv() is None and ws.close_code == 4409


async def test_commands_seq_ack_resume(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:  # noqa: F811
    s = setup
    ws = await connect(app, p, s["token"])
    await ws.hello()
    args = {"state": "off", "reason": "manual", "request_id": "r1"}
    r = await admin.post(
        cmd_url(p, s),
        json={"name": "power.set", "screen_id": s["screen"], "args": args},
        headers=SAME,
    )
    assert r.status_code == 202, r.text
    assert r.json()["seq"] == 1
    f = await ws.recv()
    assert f and f["type"] == "cmd" and f["seq"] == 1 and f["data"]["name"] == "power.set"
    assert f["data"]["screen_id"] == s["screen"] and f["id"] == f["data"]["cmd_id"]
    await admin.post(
        cmd_url(p, s), json={"name": "plan.updated", "args": {"revision": "7"}}, headers=SAME
    )
    f2 = await ws.recv()
    assert f2 and f2["seq"] == 2
    # résultat (reliable) -> ack serveur, commande terminée ; doublon ignoré mais ré-acquitté
    await ws.send(
        "cmd.result",
        {"cmd_id": f["id"], "status": "ok", "code": None, "data": {"x": 1}},
        seq=1,
        ack=2,
    )
    ack = await ws.recv()
    assert ack and ack["type"] == "ack" and ack["ack"] == 1
    await ws.send("cmd.result", {"cmd_id": f["id"], "status": "failed", "data": {}}, seq=1)
    assert (await ws.recv())["ack"] == 1  # type: ignore[index]
    rows = await sql(migrated_db_url, "SELECT seq, status FROM remote_command ORDER BY seq")
    assert [tuple(r) for r in rows] == [(1, "ok"), (2, "sent")]
    # trou de séquence -> 4400
    await ws.send("cmd.result", {"cmd_id": f["id"], "status": "ok", "data": {}}, seq=5)
    assert await ws.recv() is None and ws.close_code == 4400
    # reprise : seq 2 non acquittée par l'agent (last_server_seq=1) est rejouée
    await admin.post(
        cmd_url(p, s), json={"name": "cache.sync", "args": {"revision": "3"}}, headers=SAME
    )
    ws2 = await connect(app, p, s["token"])
    w = await ws2.hello(last_server_seq=1)
    assert w["resume_from"] == 1  # dernier seq agent acquitté (persisté)
    got = [await ws2.recv(), await ws2.recv()]
    assert [g["seq"] for g in got if g] == [2, 3]
    # commande hors liste / hors site / écran requis
    bad = await admin.post(cmd_url(p, s), json={"name": "rm.rf"}, headers=SAME)
    assert bad.json()["code"] == "agents.command_unknown"
    bad = await admin.post(cmd_url(p, s), json={"name": "power.set", "args": args}, headers=SAME)
    assert bad.json()["code"] == "agents.command_screen_required"
    bad = await admin.post(
        cmd_url(p, s),
        json={"name": "power.set", "screen_id": str(uuid4()), "args": args},
        headers=SAME,
    )
    assert bad.json()["code"] == "agents.screen_not_in_site"
    bad = await admin.post(cmd_url(p, s), json={"name": "plan.updated", "args": {}}, headers=SAME)
    assert bad.json()["code"] == "agents.command_args_invalid"


async def test_expired_command_not_sent(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:  # noqa: F811
    s = setup
    await admin.post(
        cmd_url(p, s), json={"name": "plan.updated", "args": {"revision": "1"}}, headers=SAME
    )
    await sql(
        migrated_db_url,
        "UPDATE remote_command SET expires_at = now() - interval '1 s' WHERE seq = 1",
    )
    ws = await connect(app, p, s["token"])
    await ws.hello()
    await admin.post(
        cmd_url(p, s), json={"name": "cache.sync", "args": {"revision": "2"}}, headers=SAME
    )
    f = await ws.recv()
    assert f and f["data"]["name"] == "cache.sync"
    rows = await sql(migrated_db_url, "SELECT status FROM remote_command ORDER BY seq")
    assert [r[0] for r in rows] == ["expired", "sent"]


async def test_heartbeat_and_events_bridge(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any]
) -> None:  # noqa: F811
    s = setup
    rt = app.state.runtime
    seen: list[Event] = []

    async def handler(e: Event) -> None:
        seen.append(e)

    for name in ("power.changed", "agent.connected", "agent.disconnected", "power.failed"):
        rt.bus.subscribe(name, handler)
    ws = await connect(app, p, s["token"])
    await ws.hello()
    await ws.send("heartbeat", {"uptime_s": 5, "screens": []})
    ev: dict[str, Any] = {
        "name": "power.changed",
        "version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "screen_id": s["screen"],
            "state": "off",
            "driver": "power.adb",
            "verified": True,
        },
    }
    await ws.send("event", ev, seq=1)
    assert (await ws.recv())["ack"] == 1  # type: ignore[index]
    changed = [e for e in seen if e.name == "power.changed"]
    assert changed and changed[0].origin == f"agent:{s['site']}" and changed[0].source == "agent"
    assert any(e.name == "agent.connected" for e in seen)
    lst = (await admin.get(f"{p}/api/v1/sites/{s['site']}/agents")).json()
    assert lst[0]["last_heartbeat"]["uptime_s"] == 5 and lst[0]["version"] == "1.0.0"
    # écran d'un autre site -> 4403
    other = (
        await admin.post(f"{p}/api/v1/sites", json={"name": "Autre", "slug": "autre"}, headers=SAME)
    ).json()["id"]
    foreign = (
        await admin.post(f"{p}/api/v1/screens", json={"site_id": other, "name": "X"}, headers=SAME)
    ).json()["id"]
    ev["payload"] = {**dict(ev["payload"]), "screen_id": foreign}
    await ws.send("event", ev, seq=2)
    assert await ws.recv() is None and ws.close_code == 4403
    await asyncio.sleep(0.1)
    assert len([e for e in seen if e.name == "power.changed"]) == 1
    assert any(e.name == "agent.disconnected" for e in seen)
    # nom non autorisé (événement du cœur serveur) -> 4403
    ws = await connect(app, p, s["token"])
    await ws.hello()
    await ws.send(
        "event",
        {**ev, "name": "agent.revoked", "payload": {"agent_id": s["agent"], "site_id": s["site"]}},
        seq=2,
    )
    assert await ws.recv() is None and ws.close_code == 4403


async def test_hello_timeout_and_idle(
    app: FastAPI, p: str, setup: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import vds_server.agents.session as sess

    monkeypatch.setattr(sess, "HELLO_TIMEOUT_S", 0.2)
    ws = await connect(app, p, setup["token"])
    assert await ws.recv() is None and ws.close_code == 4408
    monkeypatch.setattr(sess, "IDLE_TIMEOUT_S", 0.3)
    ws = await connect(app, p, setup["token"])
    await ws.hello()
    assert await ws.recv() is None and ws.close_code == 1001


async def test_wrong_first_frame(app: FastAPI, p: str, setup: dict[str, Any]) -> None:
    ws = await connect(app, p, setup["token"])
    await ws.send("welcome", {})
    assert await ws.recv() is None and ws.close_code == 4400
