"""Mise à jour de l'APK player (vds-core 1.6.0) : éligibilité, commande, résultat, catalogue."""

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import HELLO, FakeWS
from test_auth import SAME, admin, anon, base, clean, editor, new_client, p  # noqa: F401
from test_pairing import hdr, pair
from test_provisioning import (  # noqa: F401
    APK_BYTES,
    app,
    make_apk_dir,
    setup,
)
from vds_server.core.runtime import Runtime
from vds_server.provisioning.apk import ApkCatalog, load_entries

HELLO16 = HELLO | {"core": "1.6.0", "modules": [{"id": "power.adb", "version": "0.3.0"}]}
UPDATERS = [("power.adb", "0.3.0", frozenset({"android"}))]
HOST = "192.168.1.50"


@pytest.fixture(autouse=True)
def _updaters(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(app.state.runtime.registry, "update_modules", lambda: list(UPDATERS))


async def online(app: FastAPI, p: str, s: dict[str, Any], **hello: Any) -> FakeWS:  # noqa: F811
    from test_agents import connect

    ws = await connect(app, p, s["token"])
    await ws.hello(**(HELLO16 | hello))
    return ws


async def paired(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    s: dict[str, Any],
    code: int | None,
) -> str:
    async with new_client(app) as player:
        token = await pair(admin, player, p, s["screen"])
        if code is not None:
            body = {"app_version": "1.1.0", "app_version_code": code}
            r = await player.post(f"{p}/api/v1/player/heartbeat", json=body, headers=hdr(token))
            assert r.status_code == 200, r.text
        return token


async def set_host(admin: AsyncClient, p: str, s: dict[str, Any]) -> None:  # noqa: F811
    r = await admin.put(
        f"{p}/api/v1/config/screen/{s['screen']}/power.adb/host",
        json={"value": HOST},
        headers=SAME,
    )
    assert r.status_code == 204, r.text


async def fleet(admin: AsyncClient, p: str, s: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    r = await admin.get(f"{p}/api/v1/fleet/apk", params={"site_id": s["site"]})
    assert r.status_code == 200, r.text
    return dict(r.json())


async def row(admin: AsyncClient, p: str, s: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    return dict((await fleet(admin, p, s))["screens"][0])


def cmd_url(p: str, s: dict[str, Any], agent: str) -> str:  # noqa: F811
    return f"{p}/api/v1/sites/{s['site']}/agents/{agent}/commands"


async def agent_id(admin: AsyncClient, p: str, s: dict[str, Any]) -> str:  # noqa: F811
    r = await admin.get(f"{p}/api/v1/sites/{s['site']}/agents")
    return str(r.json()[0]["id"])


async def recv_cmd(ws: FakeWS, name: str) -> dict[str, Any]:
    """Prochaine commande `name` (les changements de config poussent d'autres commandes)."""
    while (frame := await ws.recv()) is not None:
        if frame["type"] == "cmd" and frame["data"]["name"] == name:
            return frame
    raise AssertionError("socket fermée")


async def recv_ack(ws: FakeWS) -> None:
    while (frame := await ws.recv()) is not None:
        if frame["type"] == "ack":
            return
    raise AssertionError("socket fermée")


def update_body(s: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    return {
        "name": "apk.update",
        "screen_id": s["screen"],
        "args": {"request_id": request_id or str(uuid4())},
    }


async def test_eligibility_reasons(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    out = await fleet(admin, p, s)
    assert out["available"] == {"version_code": 12, "version_name": "1.2.0", "source": "dir"}
    assert out["screens"][0] | {"screen_id": 0} == {
        "screen_id": 0,
        "installed_version_code": None,
        "installed_version_name": None,
        "state": "not_android",
        "updatable": False,
        "reason": "not_android",
    }
    await paired(admin, app, p, s, None)  # version inconnue (APK ancienne)
    r = await row(admin, p, s)
    assert (r["state"], r["updatable"], r["reason"]) == ("unknown", False, "agent_offline")
    ws = await online(app, p, s, core="1.5.0")
    assert (await row(admin, p, s))["reason"] == "agent_outdated"
    await ws.close()
    ws = await online(app, p, s, modules=[{"id": "power.adb", "version": "0.2.0"}])
    assert (await row(admin, p, s))["reason"] == "agent_outdated"
    await ws.close()
    ws = await online(app, p, s)
    rt: Runtime = app.state.runtime
    rt.registry.update_modules = lambda: []  # type: ignore[method-assign]
    assert (await row(admin, p, s))["reason"] == "module_disabled"
    rt.registry.update_modules = lambda: list(UPDATERS)  # type: ignore[method-assign]
    assert (await row(admin, p, s))["reason"] == "no_adb_host"
    await set_host(admin, p, s)
    r = await row(admin, p, s)
    assert (r["state"], r["updatable"], r["reason"]) == ("unknown", True, None)  # inconnu : permis
    entries = rt.apk_catalog._entries
    rt.apk_catalog._entries = {}
    assert (await row(admin, p, s))["reason"] == "apk_unavailable"
    rt.apk_catalog._entries = entries
    await ws.close()


async def test_installed_version_states(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    token = await paired(admin, app, p, s, 11)
    ws = await online(app, p, s)
    await set_host(admin, p, s)
    r = await row(admin, p, s)
    assert (r["installed_version_code"], r["installed_version_name"]) == (11, "1.1.0")
    assert (r["state"], r["updatable"], r["reason"]) == ("outdated", True, None)
    async with new_client(app) as player:
        hb = {"app_version_code": 12}
        assert (
            await player.post(f"{p}/api/v1/player/heartbeat", json=hb, headers=hdr(token))
        ).status_code == 200
        for bad in (0, -1, 2_100_000_001):  # heartbeat borné
            r2 = await player.post(
                f"{p}/api/v1/player/heartbeat", json={"app_version_code": bad}, headers=hdr(token)
            )
            assert r2.status_code == 422
    r = await row(admin, p, s)
    # code déclaré par l'appareil = indicatif : l'agent (inspect) arbitre
    assert (r["state"], r["updatable"], r["reason"]) == ("unknown", True, None)
    await ws.close()


async def test_roles_and_origin(
    admin: AsyncClient,  # noqa: F811
    editor: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    agent = await agent_id(admin, p, s)
    assert (await editor.get(f"{p}/api/v1/fleet/apk")).status_code == 403
    assert (
        await editor.post(cmd_url(p, s, agent), json=update_body(s), headers=SAME)
    ).status_code == 403
    r = await admin.post(
        cmd_url(p, s, agent), json=update_body(s), headers={"Origin": "https://evil.test"}
    )
    assert r.status_code == 403
    async with new_client(app) as other:
        assert (await other.get(f"{p}/api/v1/fleet/apk")).status_code == 401
        assert (
            await other.post(cmd_url(p, s, agent), json=update_body(s), headers=SAME)
        ).status_code == 401


async def test_request_lifecycle(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    s = setup
    agent = await agent_id(admin, p, s)
    await paired(admin, app, p, s, 11)
    url = cmd_url(p, s, agent)
    # agent hors ligne : raison d'éligibilité, rien en file
    r = await admin.post(url, json=update_body(s), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (422, "agent_offline")
    ws = await online(app, p, s)
    r = await admin.post(url, json=update_body(s), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (422, "no_adb_host")
    await set_host(admin, p, s)
    seen: list[Any] = []

    async def on_event(e: Any) -> None:
        seen.append((e.name, e.payload))

    for name in ("screen.apk_updated", "screen.apk_update_failed"):
        app.state.runtime.bus.subscribe(name, on_event, subscriber="test")
    # arguments inconnus refusés (l'admin n'envoie ni sha, ni taille, ni adresse)
    bad = update_body(s)
    bad["args"]["address"] = "10.0.0.9"
    assert (await admin.post(url, json=bad, headers=SAME)).status_code == 422
    no_screen = update_body(s) | {"screen_id": None}
    assert (await admin.post(url, json=no_screen, headers=SAME)).status_code == 422
    rid = str(uuid4())
    r = await admin.post(url, json=update_body(s, rid), headers=SAME)
    assert r.status_code == 202, r.text
    cid = r.json()["id"]
    frame = await recv_cmd(ws, "apk.update")
    args = frame["data"]["args"]
    assert args["apk"]["version_code"] == 12 and args["request_id"] == rid
    assert args["platform"] == "android" and args["screen_id"] == s["screen"]
    blob = json.dumps(args)
    assert "url" not in blob and HOST not in blob and "host" not in blob
    # idempotence : même request_id => même commande, rien de plus en file
    r = await admin.post(url, json=update_body(s, rid), headers=SAME)
    assert (r.status_code, r.json()["id"]) == (202, cid)
    # autre request_id pendant la mise à jour => 409
    r = await admin.post(url, json=update_body(s), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "update_running")
    assert (await row(admin, p, s))["reason"] == "update_running"
    count = await _count(migrated_db_url)
    assert count == 1
    # résultat de l'agent : version écrite sans attendre le heartbeat, événement, audit
    outcome = {"ok": True, "previous_version_code": 11, "installed_version_code": 12}
    await ws.send("cmd.result", {"cmd_id": frame["id"], "status": "ok", "data": outcome}, seq=1)
    await recv_ack(ws)
    assert seen == [
        (
            "screen.apk_updated",
            {
                "request_id": rid,
                "screen_id": s["screen"],
                "previous_version_code": 11,
                "version_code": 12,
            },
        )
    ]
    cur = await row(admin, p, s)
    assert (cur["installed_version_code"], cur["state"], cur["reason"]) == (
        12,
        "up_to_date",
        "up_to_date",
    )
    r2 = await admin.post(url, json=update_body(s), headers=SAME)
    assert (r2.status_code, r2.json()["code"]) == (409, "up_to_date")
    acts = await _sql(
        migrated_db_url,
        "SELECT action, details::text FROM audit_log WHERE action LIKE 'screen.apk_update.%'"
        " ORDER BY id",
    )
    assert [a[0] for a in acts] == ["screen.apk_update.requested", "screen.apk_update.finished"]
    assert '"to_version_code": 12' in acts[0][1] and HOST not in acts[0][1] + acts[1][1]
    await ws.close()


async def test_concurrent_same_request_id_single_command(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
    migrated_db_url: str,
) -> None:
    import asyncio

    s = setup
    agent = await agent_id(admin, p, s)
    await paired(admin, app, p, s, 11)
    ws = await online(app, p, s)
    await set_host(admin, p, s)
    body = update_body(s)
    rs = await asyncio.gather(
        *(admin.post(cmd_url(p, s, agent), json=body, headers=SAME) for _ in range(2))
    )
    assert {r.status_code for r in rs} == {202}
    assert len({r.json()["id"] for r in rs}) == 1
    assert await _count(migrated_db_url) == 1
    await ws.close()


def test_warns_without_signer_pin(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import asyncio
    import logging

    cat = ApkCatalog(make_apk_dir(tmp_path / "cat"))
    with caplog.at_level(logging.WARNING, logger="vds.apk"):
        asyncio.run(cat.load())
    assert "VDS_APK_SIGNER_SHA256" in caplog.text


async def test_failed_result_and_already_up_to_date(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    agent = await agent_id(admin, p, s)
    await paired(admin, app, p, s, 11)
    ws = await online(app, p, s)
    await set_host(admin, p, s)
    seen: list[Any] = []

    async def on_event(e: Any) -> None:
        seen.append((e.name, e.payload))

    for name in ("screen.apk_updated", "screen.apk_update_failed"):
        app.state.runtime.bus.subscribe(name, on_event, subscriber="test")
    url = cmd_url(p, s, agent)
    rid = str(uuid4())
    await admin.post(url, json=update_body(s, rid), headers=SAME)
    frame = await recv_cmd(ws, "apk.update")
    data = {"ok": False, "code": "update.android.install_incompatible"}
    await ws.send("cmd.result", {"cmd_id": frame["id"], "status": "ok", "data": data}, seq=1)
    await recv_ack(ws)
    assert seen == [
        (
            "screen.apk_update_failed",
            {
                "request_id": rid,
                "screen_id": s["screen"],
                "version_code": 12,
                "code": "update.android.install_incompatible",
            },
        )
    ]
    assert (await row(admin, p, s))["installed_version_code"] == 11  # inchangée
    # nouvelle tentative possible, résultat « déjà à jour » : version écrite, aucun événement
    seen.clear()
    await admin.post(url, json=update_body(s), headers=SAME)
    frame = await recv_cmd(ws, "apk.update")
    data = {
        "ok": True,
        "installed_version_code": 12,
        "code": "update.android.already_up_to_date",
    }
    await ws.send("cmd.result", {"cmd_id": frame["id"], "status": "ok", "data": data}, seq=2)
    await recv_ack(ws)
    assert seen == [] and (await row(admin, p, s))["installed_version_code"] == 12
    await ws.close()


async def test_apk_unavailable_is_503(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
) -> None:
    s = setup
    agent = await agent_id(admin, p, s)
    await paired(admin, app, p, s, 11)
    app.state.runtime.apk_catalog._entries = {}
    r = await admin.post(cmd_url(p, s, agent), json=update_body(s), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (503, "apk_unavailable")
    out = await fleet(admin, p, s)
    assert out["available"] is None


async def _sql(url: str, query: str) -> list[Any]:
    from test_agents import sql

    return list(await sql(url, query))


async def _count(url: str) -> int:
    return int(
        (await _sql(url, "SELECT count(*) FROM remote_command WHERE name = 'apk.update'"))[0][0]
    )


# --- catalogue : signataire épinglé, relecture sur changement de apk.json -------------------
def test_signer_pin_rejects_other_signer(tmp_path: Path) -> None:
    root = make_apk_dir(tmp_path / "cat")  # signer_sha256 = 0*64
    assert set(load_entries(root)) == {12}  # pas d'épinglage : accepté
    assert set(load_entries(root, "0" * 64)) == {12}
    assert load_entries(root, "1" * 64) == {}


async def test_catalog_reload_on_manifest_change(tmp_path: Path) -> None:
    root = make_apk_dir(tmp_path / "cat")
    now = [0.0]
    cat = ApkCatalog(root, "0" * 64, recheck_s=60, clock=lambda: now[0])
    await cat.load()
    assert cat.source == "dir" and cat.latest() is not None and cat.latest().version_code == 12  # type: ignore[union-attr]
    new = b"PK\x03\x04new-apk"
    (root / "vds-player-13.apk").write_bytes(new)
    manifest = json.loads((root / "apk.json").read_text())
    entry = dict(manifest[0]) | {
        "version_code": 13,
        "version_name": "1.3.0",
        "sha256": hashlib.sha256(new).hexdigest(),
        "size": len(new),
        "file": "vds-player-13.apk",
    }
    bad = dict(entry) | {"version_code": 14, "signer_sha256": "f" * 64}
    (root / "apk.json").write_text(json.dumps([*manifest, entry, bad]))
    await cat.refresh()  # < 60 s : pas de relecture
    assert cat.latest().version_code == 12  # type: ignore[union-attr]
    now[0] = 61.0
    await cat.refresh()
    assert cat.latest().version_code == 13  # type: ignore[union-attr]
    assert cat.get(14) is None  # signataire faux rejeté
    assert APK_BYTES  # fichier d'origine inchangé


# --- serveur <-> agent <-> power.adb, faux ADB (boucle locale, aucun réseau réel) ---


async def _agent_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frame: dict[str, Any],
    tv_port: int,
    sid: str,
) -> tuple[str, str | None, dict[str, Any]]:
    """Joue la commande émise par le serveur dans le vrai `ApkUpdateRunner` + le vrai pilote."""
    import asyncio
    import sys

    import httpx
    from vds_agent.apk_update import ApkUpdateRunner
    from vds_agent.testing import load_driver_class, make_ctx

    driver: Any = load_driver_class("power.adb")()  # pilote dynamique : duck-typing voulu
    ctx: Any = make_ctx("power.adb")  # AgentContext de test, accepté par setup() du pilote
    await driver.setup(ctx)
    mod = next(m for n, m in sys.modules.items() if n.endswith("update_android"))
    monkeypatch.setattr(mod, "ensure_lan_address", lambda ip: ip)  # le faux écoute en local
    driver._ctx.screen_configs[sid] = {"host": "127.0.0.1", "port": tv_port}
    locks: dict[str, asyncio.Lock] = {}

    def serve_apk(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=APK_BYTES)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve_apk)) as http:
        runner = ApkUpdateRunner(
            http=http,
            http_base="http://server.invalid",
            token="vds_agt_" + "z" * 43,
            data_dir=tmp_path,
            updaters=lambda _p: driver,
            screen_lock=lambda s: locks.setdefault(s, asyncio.Lock()),
            prepare=lambda _s: True,
        )
        return await runner.run(sid, frame["data"]["args"])


async def test_server_agent_roundtrip_with_fake_adb(
    admin: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    setup: dict[str, Any],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vds_agent.testing import load_fakes

    fakes = load_fakes("power.adb")
    s = setup
    agent = await agent_id(admin, p, s)
    await paired(admin, app, p, s, 11)
    ws = await online(app, p, s)
    await set_host(admin, p, s)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def on_event(e: Any) -> None:
        seen.append((e.name, e.payload))

    for name in ("screen.apk_updated", "screen.apk_update_failed"):
        app.state.runtime.bus.subscribe(name, on_event, subscriber="test")
    url = cmd_url(p, s, agent)

    async def play(mode: str, seq: int, **tv_attrs: Any) -> tuple[Any, Any]:
        r = await admin.post(url, json=update_body(s), headers=SAME)
        assert r.status_code == 202, r.text
        frame = await recv_cmd(ws, "apk.update")
        async with fakes.FakeAdbTv(mode) as tv:
            for k, v in tv_attrs.items():
                setattr(tv, k, v)
            status, code, data = await _agent_side(
                tmp_path, monkeypatch, frame, tv.port, s["screen"]
            )
        await ws.send(
            "cmd.result",
            {"cmd_id": frame["id"], "status": status, "code": code, "data": data},
            seq=seq,
        )
        await recv_ack(ws)
        return tv, (status, code)

    # TV non autorisée : échec nommé, version inchangée, commande de nouveau possible
    _, (status, code) = await play("deny", 1)
    assert (status, code) == ("failed", "update.android.adb_unauthorized")
    assert seen[-1][0] == "screen.apk_update_failed"
    assert seen[-1][1]["code"] == "update.android.adb_unauthorized"
    assert (await row(admin, p, s))["installed_version_code"] == 11
    # succès : l'agent installe via le faux ADB, le serveur enregistre la version
    tv, (status, code) = await play("open", 2, version_code=11, install_as=12)
    assert (status, code) == ("ok", None) and bytes(tv.received_apk) == APK_BYTES
    assert not any("uninstall" in c or "set-device-owner" in c for c in tv.commands)
    assert seen[-1][0] == "screen.apk_updated" and seen[-1][1]["version_code"] == 12
    cur = await row(admin, p, s)
    assert (cur["installed_version_code"], cur["state"]) == (12, "up_to_date")
    await ws.close()
