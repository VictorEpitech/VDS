"""Découverte LAN : capacités, limites, lots, résultat, expiration, aux deux VDS_BASE_PATH."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import HELLO, FakeWS, connect, sql
from test_auth import (  # noqa: F401
    ORIGIN,
    REPO_MODULES,
    SAME,
    TOKEN,
    admin,
    anon,
    base,
    clean,
    editor,
    new_client,
    p,
)
from test_diffusion import mk_screen
from vds_sdk import Event
from vds_server.core.runtime import Runtime
from vds_server.main import create_app
from vds_server.settings import Settings

MODULES = [("discovery.lan", "0.1.0")]
HELLO15 = HELLO | {"core": "1.5.0", "modules": [{"id": "discovery.lan", "version": "0.1.0"}]}


@pytest.fixture
async def app(
    base: str,
    migrated_db_url: str,
    clean: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[FastAPI]:
    monkeypatch.setenv("VDS_BOOTSTRAP_TOKEN", TOKEN)
    settings = Settings(
        base_path=base,
        database_url=migrated_db_url,
        public_origin=ORIGIN,
        modules_dir=REPO_MODULES,
        secret_key="k" * 40,  # noqa: S106
    )
    runtime = Runtime(settings)
    monkeypatch.setattr(runtime.registry, "discovery_modules", lambda: list(MODULES))
    application = create_app(settings, runtime=runtime)
    async with runtime.lifespan(application):
        assert runtime.startup_ok(), runtime.registry.startup_errors
        yield application


@pytest.fixture(autouse=True)
async def _tables_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    await sql(migrated_db_url, "TRUNCATE remote_command, discovery_scan CASCADE")


@pytest.fixture
async def setup(admin: AsyncClient, p: str) -> dict[str, Any]:  # noqa: F811
    site, screen = await mk_screen(admin, p)
    r = await admin.post(f"{p}/api/v1/sites/{site}/agents", json={"name": "Moulins"}, headers=SAME)
    assert r.status_code == 201, r.text
    return {"site": site, "screen": screen, "token": r.json()["token"], "agent": r.json()["agent"]}


def url(p: str, s: dict[str, Any], tail: str) -> str:
    return f"{p}/api/v1/sites/{s['site']}/discovery/{tail}"


async def online(app: FastAPI, p: str, s: dict[str, Any], **hello: Any) -> FakeWS:
    ws = await connect(app, p, s["token"])
    await ws.hello(**(HELLO15 | hello))
    return ws


async def caps(admin: AsyncClient, p: str, s: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    r = await admin.get(url(p, s, "capabilities"))
    assert r.status_code == 200, r.text
    return dict(r.json())


async def start(admin: AsyncClient, p: str, s: dict[str, Any]) -> str:  # noqa: F811
    r = await admin.post(url(p, s, "scans"), json={}, headers=SAME)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending"
    return str(r.json()["scan_id"])


def dev(ip: str, **over: Any) -> dict[str, Any]:
    return {"ip": ip, "platform": "android", "protocols": ["adb"], "confidence": "high"} | over


async def batch(
    app: FastAPI,
    site: str,
    scan_id: str,
    devices: list[dict[str, Any]],
    *,
    origin: str | None = None,
) -> None:
    rt: Runtime = app.state.runtime
    await rt.discovery_events.on_batch(
        Event(
            name="discovery.batch",
            payload={"scan_id": scan_id, "seq": 0, "devices": devices},
            source="agent",
            origin=origin or f"agent:{site}",
        )
    )


async def scan(admin: AsyncClient, p: str, s: dict[str, Any], sid: str) -> dict[str, Any]:  # noqa: F811
    r = await admin.get(url(p, s, f"scans/{sid}"))
    assert r.status_code == 200, r.text
    return dict(r.json())


async def result(
    app: FastAPI, sid: str, status: str = "ok", code: str | None = None, **data: Any
) -> None:
    rt: Runtime = app.state.runtime
    cmd_id = UUID(
        str(
            (
                await sql(
                    rt.settings.database_url,
                    f"SELECT cmd_id FROM discovery_scan WHERE id = '{sid}'",  # noqa: S608
                )
            )[0][0]
        )
    )
    await rt.on_agent_result(cmd_id, status, code, data)


def ok_data(sid: str, **over: Any) -> dict[str, Any]:
    return {
        "scan_id": sid,
        "networks": ["192.168.1.0/24"],
        "truncated": False,
        "device_count": 1,
        "capped": False,
        "duration_ms": 1234,
    } | over


async def test_capabilities_gating(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    missing = await admin.get(f"{p}/api/v1/sites/{uuid4()}/discovery/capabilities")
    assert missing.status_code == 404
    c = await caps(admin, p, s)
    assert (c["available"], c["reason"], c["probe_ports"]) == (False, "agent_offline", True)
    ws = await online(app, p, s, core="1.4.0")
    assert (await caps(admin, p, s))["reason"] == "agent_outdated"
    await ws.close()
    ws = await online(app, p, s, modules=[{"id": "discovery.lan", "version": "0.0.1"}])
    assert (await caps(admin, p, s))["reason"] == "agent_outdated"
    await ws.close()
    ws = await online(app, p, s)
    c = await caps(admin, p, s)
    assert (c["available"], c["reason"]) == (True, None)
    rt: Runtime = app.state.runtime
    rt.registry.discovery_modules = lambda: []  # type: ignore[method-assign]
    assert (await caps(admin, p, s))["reason"] == "module_disabled"
    await ws.close()
    # site sans agent : agent_missing, sans lire le mode d'hébergement
    r = await admin.post(f"{p}/api/v1/sites", json={"name": "Nu", "slug": "nu"}, headers=SAME)
    bare = {"site": r.json()["id"]}
    assert (await caps(admin, p, bare)) == {
        "available": False,
        "reason": "agent_missing",
        "probe_ports": True,
    }
    r = await admin.post(url(p, bare, "scans"), json={}, headers=SAME)
    assert (r.status_code, r.json()["code"], r.json()["params"]["reason"]) == (
        409,
        "discovery.unavailable",
        "agent_missing",
    )


async def test_roles_origin_body(
    admin: AsyncClient,
    editor: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    ws = await online(app, p, s)
    for tail in ("capabilities", "scans/latest", f"scans/{uuid4()}"):
        assert (await editor.get(url(p, s, tail))).status_code == 403
    assert (await editor.post(url(p, s, "scans"), json={}, headers=SAME)).status_code == 403
    assert (
        await admin.post(url(p, s, "scans"), json={}, headers={"Origin": "https://evil.test"})
    ).status_code == 403
    assert (await admin.post(url(p, s, "scans"), json={})).status_code == 403  # sans Origin
    async with new_client(app) as other:
        assert (await other.post(url(p, s, "scans"), json={}, headers=SAME)).status_code == 401
        assert (await other.get(url(p, s, "capabilities"))).status_code == 401
    for body in ({"cidrs": ["8.8.8.0/24"]}, {"targets": ["10.0.0.1"]}, {"ports": [22]}):
        r = await admin.post(url(p, s, "scans"), json=body, headers=SAME)
        assert r.status_code == 422, body
    assert (await admin.get(url(p, s, "scans/latest"))).status_code == 404
    await ws.close()


async def test_full_flow_and_known_screen(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    rt: Runtime = app.state.runtime
    seen: list[Event] = []

    async def spy(event: Event) -> None:
        seen.append(event)

    for name in ("discovery.started", "discovery.completed", "discovery.failed"):
        rt.bus.subscribe(name, spy)
    # un écran déjà configuré avec la MAC 11:22:33:44:55:66 (pilote WoL, propriété annotée)
    r = await admin.put(
        f"{p}/api/v1/config/screen/{s['screen']}/power.wol/mac",
        json={"value": "11:22:33:44:55:66"},
        headers=SAME,
    )
    assert r.status_code == 204, r.text
    ws = await online(app, p, s)
    sid = await start(admin, p, s)
    # la commande est fermée : id du scan et probe_ports seulement, aucune cible
    frame = await ws.recv()
    while frame is not None and frame["data"].get("name") == "plan.updated":
        frame = await ws.recv()  # la config d'écran a poussé un plan avant le scan
    assert frame is not None and frame["type"] == "cmd"
    assert frame["data"]["name"] == "discovery.scan"
    assert set(frame["data"]["args"]) == {"scan_id", "scope", "probe_ports"}
    assert frame["data"]["args"]["scan_id"] == sid
    assert (await scan(admin, p, s, sid))["status"] == "pending"
    # lot de progression (trame event de l'agent relayée par le pont)
    batch_event = {
        "name": "discovery.batch",
        "version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": {
            "scan_id": sid,
            "seq": 0,
            "devices": [
                dev("192.168.1.20", mac="11:22:33:44:55:66", name="<img src=x onerror=1>"),
                dev("192.168.1.21", vendor="TCL"),
            ],
        },
    }
    await ws.send("event", batch_event, seq=1)
    assert (await ws.recv())["type"] == "ack"  # type: ignore[index]
    got = await scan(admin, p, s, sid)
    assert got["status"] == "running" and len(got["devices"]) == 2
    by_ip = {d["ip"]: d for d in got["devices"]}
    assert by_ip["192.168.1.20"]["known_screen_id"] == s["screen"]
    assert by_ip["192.168.1.20"]["name"] == "<img src=x onerror=1>"  # texte brut, jamais HTML
    assert by_ip["192.168.1.21"]["known_screen_id"] is None
    # résultat final
    await ws.send(
        "cmd.result",
        {"cmd_id": frame["id"], "status": "ok", "data": ok_data(sid, device_count=2)},
        seq=2,
    )
    assert (await ws.recv())["type"] == "ack"  # type: ignore[index]
    done = await scan(admin, p, s, sid)
    assert (done["status"], done["networks"], done["truncated"], done["capped"]) == (
        "completed",
        ["192.168.1.0/24"],
        False,
        False,
    )
    latest = (await admin.get(url(p, s, "scans/latest"))).json()
    assert latest["id"] == sid and len(latest["devices"]) == 2
    names = [e.name for e in seen]
    assert names == ["discovery.started", "discovery.completed"]
    assert seen[1].payload["device_count"] == 2 and seen[1].payload["duration_ms"] == 1234
    flat = str([e.payload for e in seen])
    assert "192.168.1.2" not in flat and "11:22" not in flat  # aucun appareil dans un événement
    audits = await sql(
        migrated_db_url,
        "SELECT action, details::text FROM audit_log WHERE action LIKE 'site.discovery.%' "
        "ORDER BY id",
    )
    assert [a[0] for a in audits] == ["site.discovery.started", "site.discovery.finished"]
    assert all("192.168.1.2" not in a[1] and "11:22" not in a[1] for a in audits)
    # l'agent ne peut pas émettre les événements du noyau de découverte
    await ws.send(
        "event",
        {
            "name": "discovery.completed",
            "version": 1,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": {},
        },
        seq=3,
    )
    assert await ws.recv() is None
    await ws.close()


async def test_limits_busy_cooldown_quota(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    ws = await online(app, p, s)
    sid = await start(admin, p, s)
    r = await admin.post(url(p, s, "scans"), json={}, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "discovery.busy")
    await result(app, sid, **ok_data(sid, device_count=0))
    r = await admin.post(url(p, s, "scans"), json={}, headers=SAME)  # < 60 s après le précédent
    assert (r.status_code, r.json()["code"]) == (429, "discovery.cooldown")
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    # plus de 60 s mais 20 scans dans l'heure : quota
    await sql(migrated_db_url, "DELETE FROM discovery_scan")
    for i in range(20):
        ago = f"{2 + i} minutes"
        await sql(
            migrated_db_url,
            "INSERT INTO discovery_scan (site_id, status, probe_ports, created_at, finished_at) "  # noqa: S608
            f"VALUES ('{s['site']}', 'completed', true, now() - interval '{ago}', now())",
        )
    r = await admin.post(url(p, s, "scans"), json={}, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (429, "discovery.quota")
    assert int(r.headers["Retry-After"]) >= 1
    await sql(migrated_db_url, "DELETE FROM discovery_scan")
    await start(admin, p, s)  # tout est libre à nouveau
    await ws.close()


async def test_batch_validation(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    ws = await online(app, p, s)
    sid = await start(admin, p, s)
    # lot d'une autre origine, ou d'un scan inconnu : ignoré
    await batch(app, s["site"], sid, [dev("192.168.1.5")], origin=f"agent:{uuid4()}")
    await batch(app, s["site"], str(uuid4()), [dev("192.168.1.5")])
    assert (await scan(admin, p, s, sid))["devices"] == []
    # adresses hors RFC1918 ou invalides : rejetées
    bad = [dev("8.8.8.8"), dev("100.64.0.9"), dev("169.254.1.1"), dev("192.168.1.6", mac="zz")]
    await batch(app, s["site"], sid, [*bad, dev("10.1.2.3")])
    got = await scan(admin, p, s, sid)
    assert [d["ip"] for d in got["devices"]] == ["10.1.2.3"] and got["status"] == "running"
    # agent remplacé (le scan n'appartient plus à l'agent courant) : lot ignoré
    await sql(migrated_db_url, "UPDATE discovery_scan SET agent_id = NULL")
    await batch(app, s["site"], sid, [dev("192.168.1.7")])
    assert len((await scan(admin, p, s, sid))["devices"]) == 1
    await sql(migrated_db_url, f"UPDATE discovery_scan SET agent_id = '{s['agent']['id']}'")  # noqa: S608
    # plafond de 128 appareils (16 par lot)
    for k in range(9):
        await batch(app, s["site"], sid, [dev(f"192.168.2.{k * 16 + i + 1}") for i in range(16)])
    got = await scan(admin, p, s, sid)
    assert len(got["devices"]) == 128
    await batch(app, s["site"], sid, [dev("192.168.3.1")])
    assert len((await scan(admin, p, s, sid))["devices"]) == 128
    # mise à jour d'un appareil déjà vu : pas de doublon
    await batch(app, s["site"], sid, [dev("10.1.2.3", vendor="Sony")])
    # résultat : seuls les appareils dans les réseaux déclarés restent
    await result(app, sid, **ok_data(sid, networks=["192.168.2.0/24"], device_count=128))
    done = await scan(admin, p, s, sid)
    assert done["status"] == "completed" and done["capped"] is True
    assert "10.1.2.3" not in {d["ip"] for d in done["devices"]}
    assert all(d["ip"].startswith("192.168.2.") for d in done["devices"])
    # après la fin, un lot est ignoré
    await batch(app, s["site"], sid, [dev("192.168.2.250")])
    assert "192.168.2.250" not in {d["ip"] for d in (await scan(admin, p, s, sid))["devices"]}
    await ws.close()


async def test_result_failures_and_invalid(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    ws = await online(app, p, s)
    for code, expected in (
        ("discovery.no_private_network", "discovery.no_private_network"),
        ("unknown_command", "discovery.agent_outdated"),
        ("Bad Code!", "discovery.internal"),
    ):
        sid = await start(admin, p, s)
        await result(app, sid, "failed", code)
        got = await scan(admin, p, s, sid)
        assert (got["status"], got["error_code"]) == ("failed", expected)
        await sql(
            migrated_db_url, "UPDATE discovery_scan SET created_at = now() - interval '5 min'"
        )
    for data in (
        ok_data("00000000-0000-0000-0000-000000000000"),  # autre scan_id
        ok_data("x", networks=["192.168.0.0/16"]),  # plus large que /22
        ok_data("x", networks=["8.8.8.0/24"]),  # public
        {"nope": 1},
    ):
        sid = await start(admin, p, s)
        if data.get("scan_id") == "x":
            data = data | {"scan_id": sid}
        await result(app, sid, **data)
        got = await scan(admin, p, s, sid)
        assert (got["status"], got["error_code"]) == ("failed", "discovery.internal")
        await sql(
            migrated_db_url, "UPDATE discovery_scan SET created_at = now() - interval '5 min'"
        )
    await ws.close()


async def test_timeout_without_result(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    rt: Runtime = app.state.runtime
    seen: list[Event] = []

    async def spy(event: Event) -> None:
        seen.append(event)

    rt.bus.subscribe("discovery.failed", spy)
    ws = await online(app, p, s)
    sid = await start(admin, p, s)
    assert (await scan(admin, p, s, sid))["status"] == "pending"
    await sql(migrated_db_url, "UPDATE discovery_scan SET created_at = now() - interval '61 s'")
    got = await scan(admin, p, s, sid)
    assert (got["status"], got["error_code"]) == ("failed", "discovery.timeout")
    assert [e.payload["code"] for e in seen] == ["discovery.timeout"]
    cmd = await sql(migrated_db_url, "SELECT status FROM remote_command")
    assert cmd[0][0] in ("expired", "sent")  # jamais exécutée après l'échéance
    # un résultat tardif de l'agent ne ressuscite pas le scan
    await result(app, sid, **ok_data(sid))
    assert (await scan(admin, p, s, sid))["status"] == "failed"
    await start(admin, p, s)  # le scan échu ne bloque plus le site
    await ws.close()


async def test_scan_scoped_to_site_and_purged(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    ws = await online(app, p, s)
    sid = await start(admin, p, s)
    r = await admin.post(f"{p}/api/v1/sites", json={"name": "Autre", "slug": "autre"}, headers=SAME)
    other = {"site": r.json()["id"]}
    assert (await admin.get(url(p, other, f"scans/{sid}"))).status_code == 404
    assert (await admin.get(url(p, other, "scans/latest"))).status_code == 404
    # purge à 24 h : le dernier scan trop vieux n'est plus servi
    await sql(migrated_db_url, "UPDATE discovery_scan SET created_at = now() - interval '25 h'")
    assert (await admin.get(url(p, s, "scans/latest"))).status_code == 404
    await start(admin, p, s)
    assert (await sql(migrated_db_url, "SELECT count(*) FROM discovery_scan"))[0][0] == 1
    await asyncio.sleep(0)
    await ws.close()


def test_migration_down_up(migrated_db_url: str, alembic_cfg: Config) -> None:
    command.downgrade(alembic_cfg, "0009_remote_command_keep_seq")
    command.upgrade(alembic_cfg, "heads")


async def test_generic_command_endpoint_refuses_scan(
    admin: AsyncClient,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    r = await admin.post(
        f"{p}/api/v1/sites/{s['site']}/agents/{s['agent']['id']}/commands",
        json={"name": "discovery.scan", "args": {"scan_id": str(uuid4()), "probe_ports": True}},
        headers=SAME,
    )
    assert (r.status_code, r.json()["code"]) == (422, "agents.command_internal")
