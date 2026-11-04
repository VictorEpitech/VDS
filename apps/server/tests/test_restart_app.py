"""Relance de l'application (vds-core 1.14.0) : exécutant auto/player/agent, disponibilité,
résultat de l'agent, droits, idempotence, aux deux VDS_BASE_PATH."""
# ruff: noqa: F811

from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_apk_update import HOST, online, recv_ack, recv_cmd, set_host
from test_auth import SAME, admin, anon, base, clean, editor, new_client, p  # noqa: F401
from test_pairing import hdr, pair
from test_perimeter import sql
from test_provisioning import app, setup  # noqa: F401
from vds_server.core.runtime import Runtime

RESTARTERS = [("power.adb", "0.4.0", frozenset({"android"}))]
HELLO14 = {"core": "1.14.0", "modules": [{"id": "power.adb", "version": "0.4.0"}]}
CAPS = ["cmd.reload", "cmd.restart.app", "cmd.restart.device"]


@pytest.fixture(autouse=True)
def _restarters(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state.runtime.registry, "app_restart_modules", lambda: list(RESTARTERS))


async def player(app: FastAPI, admin: AsyncClient, p: str, s: dict[str, Any], caps: Any) -> None:
    async with new_client(app) as pl:
        token = await pair(admin, pl, p, s["screen"])
        body = {"app_version": "1.11.0", "capabilities": caps} if caps else {"app_version": "1"}
        r = await pl.post(f"{p}/api/v1/player/heartbeat", json=body, headers=hdr(token))
        assert r.status_code == 200, r.text


async def screen(admin: AsyncClient, p: str, s: dict[str, Any]) -> dict[str, Any]:
    r = await admin.get(f"{p}/api/v1/screens/{s['screen']}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def send(
    c: AsyncClient, p: str, s: dict[str, Any], args: dict[str, Any] | None = None, key: str = ""
) -> Any:
    body = {
        "name": "restart",
        "args": {"scope": "app"} | (args or {}),
        "request_key": key or uuid4().hex,
    }
    return await c.post(f"{p}/api/v1/screens/{s['screen']}/commands", json=body, headers=SAME)


async def stale(url: str, s: dict[str, Any]) -> None:
    await sql(
        url,
        "UPDATE screen_device SET last_seen_at = now() - interval '10 minutes'"
        " WHERE screen_id = :s",
        s=s["screen"],
    )


async def test_availability_reasons(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:
    s = setup
    assert (await screen(admin, p, s))["restart_app"]["reason"] == "no_capability"
    await player(app, admin, p, s, CAPS)
    ra = (await screen(admin, p, s))["restart_app"]
    assert (ra["available"], ra["via"], ra["reason"]) == (True, ["player"], None)
    await stale(migrated_db_url, s)
    ra = (await screen(admin, p, s))["restart_app"]
    assert (ra["available"], ra["reason"]) == (False, "agent_offline")
    ws = await online(app, p, s, **(HELLO14 | {"core": "1.13.0"}))
    assert (await screen(admin, p, s))["restart_app"]["reason"] == "agent_outdated"
    await ws.close()
    ws = await online(app, p, s, **HELLO14)
    assert (await screen(admin, p, s))["restart_app"]["reason"] == "no_adb_host"
    rt: Runtime = app.state.runtime
    rt.registry.app_restart_modules = lambda: []  # type: ignore[method-assign]
    assert (await screen(admin, p, s))["restart_app"]["reason"] == "module_disabled"
    rt.registry.app_restart_modules = lambda: list(RESTARTERS)  # type: ignore[method-assign]
    await set_host(admin, p, s)
    ra = (await screen(admin, p, s))["restart_app"]
    assert (ra["available"], ra["via"], ra["reason"]) == (True, ["agent"], None)
    await sql(
        migrated_db_url,
        "UPDATE screen_device SET last_seen_at = now() WHERE screen_id = :s",
        s=s["screen"],
    )
    assert (await screen(admin, p, s))["restart_app"]["via"] == ["player", "agent"]
    await ws.close()


async def test_player_without_capability_is_unavailable(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:
    s = setup
    await player(app, admin, p, s, None)  # vieux player : seul `reload`
    assert (await screen(admin, p, s))["restart_app"]["reason"] == "agent_offline"
    await sql(migrated_db_url, "UPDATE screen_device SET platform='web'")
    assert (await screen(admin, p, s))["restart_app"]["reason"] == "no_capability"


async def test_auto_player_and_explicit(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any]
) -> None:
    s = setup
    await player(app, admin, p, s, CAPS)
    r = await send(admin, p, s)
    assert r.status_code == 201, r.text
    assert (r.json()["executor"], r.json()["status"]) == ("player", "queued")
    assert (await send(admin, p, s, {"via": "player"})).status_code == 201
    r = await send(admin, p, s, {"via": "agent"})
    assert (r.status_code, r.json()["code"]) == (409, "agent_offline")  # agent hors ligne
    r = await send(admin, p, s, {"scope": "device", "via": "agent"})
    assert (r.status_code, r.json()["code"]) == (422, "cmd.args_invalid")


async def test_auto_unavailable(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:
    s = setup
    await player(app, admin, p, s, CAPS)
    await stale(migrated_db_url, s)
    r = await send(admin, p, s)
    assert (r.status_code, r.json()["code"]) == (422, "restart_app_unavailable")
    assert (await send(admin, p, s, {"via": "agent"})).status_code == 409


async def test_agent_path_result_idempotence_and_audit(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:
    s = setup
    await player(app, admin, p, s, CAPS)
    await set_host(admin, p, s)
    await stale(migrated_db_url, s)
    ws = await online(app, p, s, **HELLO14)
    seen: list[tuple[str, str | None]] = []

    async def on_done(event: Any) -> None:
        seen.append((event.payload["status"], event.payload["code"]))

    app.state.runtime.bus.subscribe("player.command_completed", on_done, subscriber="t")
    r = await send(admin, p, s, key="k" * 12)
    assert r.status_code == 201, r.text
    cmd = r.json()
    assert (cmd["executor"], cmd["status"]) == ("agent", "sent")
    frame = await recv_cmd(ws, "app.restart")
    args = frame["data"]["args"]
    assert args == {"request_id": cmd["id"], "screen_id": s["screen"], "platform": "android"}
    assert HOST not in str(args)
    again = await send(admin, p, s, key="k" * 12)  # idempotence : rien de plus en file
    assert (again.status_code, again.json()["id"]) == (200, cmd["id"])
    # jamais livrée au player
    rows = await sql(
        migrated_db_url, "SELECT count(*) FROM remote_command WHERE name='app.restart'"
    )
    assert rows[0][0] == 1
    await ws.send(
        "cmd.result",
        {"cmd_id": frame["id"], "status": "ok", "data": {"ok": False, "code": "app.restart.busy"}},
        seq=1,
    )
    await recv_ack(ws)
    got = (await admin.get(f"{p}/api/v1/screens/{s['screen']}/commands")).json()[0]
    assert (got["status"], got["code"], got["executor"]) == ("failed", "app.restart.busy", "agent")
    assert seen == [("failed", "app.restart.busy")]
    aud = await sql(
        migrated_db_url,
        "SELECT details->>'executor' FROM audit_log WHERE action='screen.command.requested'",
    )
    assert aud[-1][0] == "agent"
    await ws.close()


async def test_agent_result_ok(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any], migrated_db_url: str
) -> None:
    s = setup
    await player(app, admin, p, s, None)
    await set_host(admin, p, s)
    ws = await online(app, p, s, **HELLO14)
    r = await send(admin, p, s)  # auto : player sans capacité => agent
    assert (r.status_code, r.json()["executor"]) == (201, "agent")
    frame = await recv_cmd(ws, "app.restart")
    await ws.send(
        "cmd.result", {"cmd_id": frame["id"], "status": "ok", "data": {"ok": True}}, seq=1
    )
    await recv_ack(ws)
    got = (await admin.get(f"{p}/api/v1/screens/{s['screen']}/commands")).json()[0]
    assert (got["status"], got["code"]) == ("ok", None)
    await ws.close()


async def test_roles(
    admin: AsyncClient, editor: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any]
) -> None:
    s = setup
    await player(app, admin, p, s, CAPS)
    r = await send(editor, p, s)
    assert r.status_code == 403
    cross = await admin.post(
        f"{p}/api/v1/screens/{s['screen']}/commands",
        json={"name": "restart", "args": {"scope": "app"}, "request_key": uuid4().hex},
        headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
    )
    assert cross.status_code == 403
