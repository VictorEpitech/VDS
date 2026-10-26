"""Provisioning d'écran : capacités, session, ticket à usage unique, APK, aux deux VDS_BASE_PATH."""

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from test_agents import HELLO, FakeWS, auth, connect, sql
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
from test_pairing import DEVICE, hdr
from vds_server.core.runtime import Runtime
from vds_server.main import create_app
from vds_server.provisioning.apk import load_entries
from vds_server.settings import Settings

APK_BYTES = b"PK\x03\x04fake-apk-content"
MODULES = [("power.adb", "0.2.0", frozenset({"android"}))]
HELLO13 = HELLO | {"core": "1.3.0", "modules": [{"id": "power.adb", "version": "0.2.0"}]}


def make_apk_dir(root: Path) -> Path:
    root.mkdir()
    (root / "vds-player-12.apk").write_bytes(APK_BYTES)
    manifest = [
        {
            "package": "app.vds.player",
            "version_code": 12,
            "version_name": "1.2.0",
            "sha256": hashlib.sha256(APK_BYTES).hexdigest(),
            "size": len(APK_BYTES),
            "signer_sha256": "0" * 64,
            "file": "vds-player-12.apk",
        }
    ]
    (root / "apk.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture
async def app(
    base: str,
    migrated_db_url: str,
    clean: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[FastAPI]:
    monkeypatch.setenv("VDS_BOOTSTRAP_TOKEN", TOKEN)
    settings = Settings(
        base_path=base,
        database_url=migrated_db_url,
        public_origin=ORIGIN,
        modules_dir=REPO_MODULES,
        secret_key="k" * 40,  # noqa: S106
        apk_dir=make_apk_dir(tmp_path / "apk"),
    )
    runtime = Runtime(settings)
    monkeypatch.setattr(runtime.registry, "provision_modules", lambda: list(MODULES))
    application = create_app(settings, runtime=runtime)
    async with runtime.lifespan(application):
        assert runtime.startup_ok(), runtime.registry.startup_errors
        yield application


@pytest.fixture(autouse=True)
async def _queue_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    await sql(migrated_db_url, "TRUNCATE remote_command, pairing_request, screen_device CASCADE")


@pytest.fixture
async def setup(admin: AsyncClient, p: str) -> dict[str, Any]:  # noqa: F811
    site, screen = await mk_screen(admin, p)
    r = await admin.post(f"{p}/api/v1/sites/{site}/agents", json={"name": "Moulins"}, headers=SAME)
    assert r.status_code == 201, r.text
    return {"site": site, "screen": screen, "token": r.json()["token"]}


def body(**over: Any) -> dict[str, Any]:
    return {"platform": "android", "address": "192.168.1.50"} | over


async def online(app: FastAPI, p: str, s: dict[str, Any], **hello: Any) -> FakeWS:
    ws = await connect(app, p, s["token"])
    await ws.hello(**(HELLO13 | hello))
    return ws


async def caps(admin: AsyncClient, p: str, s: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    r = await admin.get(f"{p}/api/v1/provisioning/capabilities", params={"site_id": s["site"]})
    assert r.status_code == 200, r.text
    return dict(r.json())


async def start(admin: AsyncClient, p: str, s: dict[str, Any], **over: Any) -> dict[str, Any]:  # noqa: F811
    r = await admin.post(
        f"{p}/api/v1/screens/{s['screen']}/provision", json=body(**over), headers=SAME
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def event(ws: FakeWS, seq: int, payload: dict[str, Any]) -> None:
    ev = {
        "name": "screen.provision_progressed",
        "version": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    await ws.send("event", ev, seq=seq)
    ack = await ws.recv()
    assert ack is not None and ack["type"] == "ack"


async def test_capabilities_gating(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    c = await caps(admin, p, s)
    assert (c["available"], c["reason"], c["platforms"]) == (False, "agent_offline", [])
    assert c["apk"] == {"version_name": "1.2.0", "version_code": 12}
    ws = await online(app, p, s, core="1.2.0")  # agent < 1.3
    assert (await caps(admin, p, s))["reason"] == "agent_outdated"
    await ws.close()
    ws = await online(app, p, s, modules=[{"id": "power.adb", "version": "0.1.0"}])
    assert (await caps(admin, p, s))["reason"] == "agent_outdated"  # module de l'agent trop vieux
    await ws.close()
    ws = await online(app, p, s)
    c = await caps(admin, p, s)
    assert c["available"] is True and c["reason"] is None and c["platforms"] == ["android"]
    rt: Runtime = app.state.runtime
    rt.registry.provision_modules = lambda: []  # type: ignore[method-assign]
    assert (await caps(admin, p, s))["reason"] == "module_disabled"
    await ws.close()


async def test_roles_and_origin(
    admin: AsyncClient,
    editor: AsyncClient,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    url = f"{p}/api/v1/screens/{s['screen']}/provision"
    assert (await editor.post(url, json=body(), headers=SAME)).status_code == 403
    assert (
        await editor.get(f"{p}/api/v1/provisioning/capabilities?site_id={s['site']}")
    ).status_code == 403
    assert (
        await admin.post(url, json=body(), headers={"Origin": "https://evil.test"})
    ).status_code == 403
    async with new_client(admin._transport.app) as other:  # type: ignore[attr-defined]
        assert (await other.post(url, json=body(), headers=SAME)).status_code == 401


async def test_validation_and_unavailable(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    url = f"{p}/api/v1/screens/{s['screen']}/provision"
    r = await admin.post(url, json=body(), headers=SAME)  # agent hors ligne
    assert (r.status_code, r.json()["code"], r.json()["params"]["reason"]) == (
        409,
        "provision.unavailable",
        "agent_offline",
    )
    ws = await online(app, p, s)
    for addr, code in (
        ("8.8.8.8", "provision.address_not_lan"),
        ("127.0.0.1", "provision.address_not_lan"),
        ("nope", "provision.address_invalid"),
    ):
        r = await admin.post(url, json=body(address=addr), headers=SAME)
        assert (r.status_code, r.json()["code"]) == (422, code)
    r = await admin.post(url, json=body(platform="tizen"), headers=SAME)
    assert r.status_code == 409
    missing = "00000000-0000-0000-0000-000000000000"
    r = await admin.post(f"{p}/api/v1/screens/{missing}/provision", json=body(), headers=SAME)
    assert r.status_code == 404
    await ws.close()


async def test_full_flow_ticket_single_use(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],
    migrated_db_url: str,  # noqa: F811
) -> None:
    s = setup
    ws = await online(app, p, s)
    out = await start(admin, p, s, device_owner="skip")
    assert (
        out["status"] == "queued" and out["steps"] == [] and "pairing_ticket" not in json.dumps(out)
    )
    pid = out["id"]
    frame = await ws.recv()
    assert frame and frame["type"] == "cmd"
    data = frame["data"]
    assert data["name"] == "screen.provision" and data["screen_id"] == s["screen"]
    args = data["args"]
    ticket = args["pairing_ticket"]
    assert args["address"] == "192.168.1.50" and args["device_owner"] == "skip"
    assert args["apk"]["version_code"] == 12 and "url" not in json.dumps(args["apk"])
    assert args["server_url"] == ORIGIN + ("" if p == "" else p)
    # au repos : ni ticket en clair en base (commande, session), ni dans la sortie API
    stored = await sql(migrated_db_url, "SELECT args::text FROM remote_command")
    assert ticket not in stored[0][0] and "pairing_ticket_enc" in stored[0][0]
    row = await sql(migrated_db_url, "SELECT ticket_hash, status FROM screen_provision")
    assert bytes(row[0][0]) == hashlib.sha256(ticket.encode()).digest()
    # une seule session active par écran
    r = await admin.post(f"{p}/api/v1/screens/{s['screen']}/provision", json=body(), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "provision.session_active")
    # progression relayée par l'agent
    base_payload = {"provision_id": pid, "screen_id": s["screen"], "code": None}
    await event(ws, 1, base_payload | {"step": "connect", "status": "ok"})
    await event(ws, 2, base_payload | {"step": "authorize", "status": "waiting_user"})
    got = (await admin.get(f"{p}/api/v1/provisioning/{pid}")).json()
    assert got["status"] == "waiting_user"
    assert [(x["step"], x["status"]) for x in got["steps"]] == [
        ("connect", "ok"),
        ("authorize", "waiting_user"),
    ]
    # résultat final de l'agent : en attente de l'écran
    await ws.send(
        "cmd.result",
        {
            "cmd_id": frame["id"],
            "status": "ok",
            "data": {"ok": True, "kiosk": "soft", "installed_version_code": 12},
        },
        seq=3,
    )
    assert (await ws.recv())["type"] == "ack"  # type: ignore[index]
    got = (await admin.get(f"{p}/api/v1/provisioning/{pid}")).json()
    assert got["outcome"]["kiosk"] == "soft" and got["status"] in ("waiting_user", "running")
    # le player échange le ticket (additif à /player/pairings)
    async with new_client(app) as player:
        r = await player.post(
            f"{p}/api/v1/player/pair", json={"pairing_ticket": ticket, "device": DEVICE}
        )
        assert r.status_code == 200, r.text
        token = r.json()["device_token"]
        assert r.json()["screen_id"] == s["screen"]
        assert (await admin.get(f"{p}/api/v1/provisioning/{pid}")).json()[
            "status"
        ] == "waiting_online"
        # ticket rejoué : refusé
        r = await player.post(
            f"{p}/api/v1/player/pair", json={"pairing_ticket": ticket, "device": DEVICE}
        )
        assert (r.status_code, r.json()["code"]) == (401, "errors.pairing_ticket_invalid")
        # premier heartbeat : session validée
        seen: list[str] = []

        async def on_event(e: Any) -> None:
            seen.append(e.name)

        app.state.runtime.bus.subscribe("screen.provisioned", on_event, subscriber="test")
        assert (
            await player.post(f"{p}/api/v1/player/heartbeat", json={}, headers=hdr(token))
        ).status_code == 200
    got = (await admin.get(f"{p}/api/v1/provisioning/{pid}")).json()
    assert got["status"] == "done" and got["finished_at"] and seen == ["screen.provisioned"]
    # ticket effacé : de la session et de la commande
    row = await sql(migrated_db_url, "SELECT ticket_hash FROM screen_provision")
    assert row[0][0] is None
    stored = await sql(migrated_db_url, "SELECT args::text FROM remote_command")
    assert "pairing_ticket_enc" not in stored[0][0]
    actions = [r[0] for r in await sql(migrated_db_url, "SELECT action FROM audit_log ORDER BY id")]
    assert "screen.provision.started" in actions and "screen.provision.finished" in actions
    details = await sql(
        migrated_db_url,
        "SELECT details::text FROM audit_log WHERE action LIKE 'screen.provision.%'",
    )
    assert all(ticket not in d[0] for d in details)
    # écran déjà appairé : refus, sauf reprovision
    r = await admin.post(f"{p}/api/v1/screens/{s['screen']}/provision", json=body(), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "provision.screen_paired")
    r = await admin.post(
        f"{p}/api/v1/screens/{s['screen']}/provision", json=body(reprovision=True), headers=SAME
    )
    assert r.status_code == 201
    await ws.close()


async def test_failure_retry_cancel_and_expiry(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],
    migrated_db_url: str,  # noqa: F811
) -> None:
    s = setup
    ws = await online(app, p, s)
    out = await start(admin, p, s)
    pid = out["id"]
    frame = await ws.recv()
    assert frame
    base_payload = {
        "provision_id": pid,
        "screen_id": s["screen"],
        "code": "provision.android.accounts_present",
    }
    await event(ws, 1, base_payload | {"step": "device_owner", "status": "failed"})
    await ws.send(
        "cmd.result",
        {
            "cmd_id": frame["id"],
            "status": "failed",
            "code": "provision.android.accounts_present",
            "data": {},
        },
        seq=2,
    )
    assert (await ws.recv())["type"] == "ack"  # type: ignore[index]
    got = (await admin.get(f"{p}/api/v1/provisioning/{pid}")).json()
    assert got["status"] == "failed" and got["code"] == "provision.android.accounts_present"
    # reprise à une étape : nouvelle commande, nouveau ticket
    r = await admin.post(
        f"{p}/api/v1/provisioning/{pid}/retry", json={"from_step": "device_owner"}, headers=SAME
    )
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["steps"] == []
    frame2 = await ws.recv()
    assert frame2 and frame2["data"]["args"]["from_step"] == "device_owner"
    assert frame2["data"]["args"]["pairing_ticket"] != ""
    # retry d'une session non échouée : refusé
    r = await admin.post(f"{p}/api/v1/provisioning/{pid}/retry", json={}, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "provision.not_retryable")
    # annulation : le ticket n'est plus valable
    ticket = frame2["data"]["args"]["pairing_ticket"]
    r = await admin.post(f"{p}/api/v1/provisioning/{pid}/cancel", headers=SAME)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    async with new_client(app) as player:
        r = await player.post(
            f"{p}/api/v1/player/pair", json={"pairing_ticket": ticket, "device": DEVICE}
        )
        assert r.status_code == 401
    assert (
        await admin.post(f"{p}/api/v1/provisioning/{pid}/cancel", headers=SAME)
    ).status_code == 409
    # expiration (30 min) : ticket refusé, session échouée au prochain suivi
    out = await start(admin, p, s)
    frame3 = await ws.recv()
    assert frame3
    ticket = frame3["data"]["args"]["pairing_ticket"]
    await sql(
        migrated_db_url,
        "UPDATE screen_provision SET ticket_expires_at = now() - interval '1 minute'"
        " WHERE status = 'queued'",
    )
    async with new_client(app) as player:
        r = await player.post(
            f"{p}/api/v1/player/pair", json={"pairing_ticket": ticket, "device": DEVICE}
        )
        assert r.status_code == 401
    got = (await admin.get(f"{p}/api/v1/provisioning/{out['id']}")).json()
    assert got["status"] == "failed" and got["code"] == "provision.ticket_expired"
    assert (
        await admin.get(f"{p}/api/v1/provisioning/00000000-0000-0000-0000-000000000000")
    ).status_code == 404
    await ws.close()


async def test_foreign_progress_ignored(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    ws = await online(app, p, s)
    out = await start(admin, p, s)
    await ws.recv()
    # progression pour un provision_id inconnu : ignorée sans effet
    unknown = "00000000-0000-0000-0000-0000000000aa"
    await event(
        ws,
        1,
        {
            "provision_id": unknown,
            "screen_id": s["screen"],
            "step": "connect",
            "status": "ok",
            "code": None,
        },
    )
    got = (await admin.get(f"{p}/api/v1/provisioning/{out['id']}")).json()
    assert got["steps"] == []
    await ws.close()


async def test_apk_service(app: FastAPI, p: str, setup: dict[str, Any], tmp_path: Path) -> None:
    s = setup
    url = f"{p}/api/v1/agent/apk/android"
    async with new_client(app) as c:
        assert (await c.get(f"{url}/12")).status_code == 401
        assert (await c.get(f"{url}/12", headers=auth("vds_agt_inconnu"))).status_code == 401
        r = await c.get(f"{url}/12", headers=auth(s["token"]))
        assert r.status_code == 200 and r.content == APK_BYTES
        assert r.headers["content-type"] == "application/vnd.android.package-archive"
        etag = hashlib.sha256(APK_BYTES).hexdigest()
        assert r.headers["etag"] == f'"{etag}"'
        r = await c.get(f"{url}/12", headers=auth(s["token"]) | {"If-None-Match": f'"{etag}"'})
        assert r.status_code == 304
        assert (await c.get(f"{url}/13", headers=auth(s["token"]))).status_code == 404
        assert (await c.get(f"{url}/0", headers=auth(s["token"]))).status_code == 422
        assert (await c.get(f"{url}/abc", headers=auth(s["token"]))).status_code == 422


def test_apk_catalog_rejects_unsafe_entries(tmp_path: Path) -> None:
    root = make_apk_dir(tmp_path / "apk")
    (tmp_path / "secret.apk").write_bytes(APK_BYTES)
    good: dict[str, Any] = json.loads((root / "apk.json").read_text())[0]
    sha = good["sha256"]

    def entry(code: int, **over: Any) -> dict[str, Any]:
        return good | {"version_code": code} | over

    (root / "bad.apk").write_bytes(b"autre contenu")
    manifest = [
        good,
        entry(13, file="../secret.apk"),  # sortie du dossier
        entry(14, file="/etc/passwd"),
        entry(15, file="bad.apk", size=13),  # sha256 différent
        entry(16, package="evil.app"),
        entry(17, sha256="z" * 64),
        entry(18, file="absent.apk"),
        entry(19, file="vds-player-12.apk", size=3),  # taille différente
    ]
    (root / "apk.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert sha and set(load_entries(root)) == {12}


def tv_client(app: FastAPI, host: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=(host, 40000)), base_url=ORIGIN)


async def test_internal_command_refused_on_generic_route(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any]
) -> None:
    s = setup
    ws = await online(app, p, s)
    r = await admin.get(f"{p}/api/v1/sites/{s['site']}/agents")
    agent = r.json()[0]["id"]
    args = {
        "server_url": "https://serveur-tiers.example",
        "pairing_ticket": "x" * 43,
        "address": "192.168.1.50",
    }
    r = await admin.post(
        f"{p}/api/v1/sites/{s['site']}/agents/{agent}/commands",
        json={"name": "screen.provision", "args": args, "screen_id": s["screen"]},
        headers=SAME,
    )
    assert (r.status_code, r.json()["code"]) == (422, "agents.command_internal")
    assert await sql_count_commands(app) == 0
    await ws.close()


async def sql_count_commands(app: FastAPI) -> int:
    url = app.state.runtime.settings.database_url
    return int((await sql(url, "SELECT count(*) FROM remote_command"))[0][0])


async def test_ticket_rejected_outside_session_network(
    admin: AsyncClient, app: FastAPI, p: str, setup: dict[str, Any]
) -> None:
    s = setup
    ws = await online(app, p, s)
    await start(admin, p, s, device_owner="skip")
    frame = await ws.recv()
    assert frame is not None
    ticket = frame["data"]["args"]["pairing_ticket"]
    pair = {"pairing_ticket": ticket, "device": DEVICE}
    # poste du même LAN mais autre que la TV : refusé, ticket non consommé
    async with tv_client(app, "192.168.1.77") as other:
        r = await other.post(f"{p}/api/v1/player/pair", json=pair)
        assert (r.status_code, r.json()["code"]) == (401, "errors.pairing_ticket_invalid")
    # IPv4 mappée en IPv6 de la TV : accepté (le ticket n'était pas brûlé)
    async with tv_client(app, "::ffff:192.168.1.50") as tv:
        r = await tv.post(f"{p}/api/v1/player/pair", json=pair)
        assert r.status_code == 200, r.text
    await ws.close()


def test_lan_client_allowed_rules() -> None:
    from vds_server.provisioning.service import lan_client_allowed as ok

    assert ok("192.168.1.50", "192.168.1.50")
    assert not ok("192.168.1.51", "192.168.1.50")
    assert not ok("10.0.0.5", "192.168.1.50")
    assert ok("127.0.0.1", "192.168.1.50")  # proxy local
    assert ok("8.8.8.8", "192.168.1.50")  # NAT du site : limite acceptée
    assert not ok("garbage", "192.168.1.50")
    assert ok("192.168.1.50", "192.168.1.50/32")


def test_ticket_windows_are_short() -> None:
    from vds_server.provisioning.service import ONLINE_TIMEOUT
    from vds_server.provisioning.tickets import TICKET_TTL_S

    assert TICKET_TTL_S == 600 and ONLINE_TIMEOUT.total_seconds() == 300
