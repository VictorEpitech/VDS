"""Lot I2 : commandes player (création, périmètre, livraison WS/HTTP, résultats, expiration,
annulation, idempotence, plafonds, audit, événements), aux deux VDS_BASE_PATH."""
# ruff: noqa: F811

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import FakeWS
from test_auth import SAME, admin, anon, app, base, clean, new_client, p  # noqa: F401
from test_diffusion import _content_types  # noqa: F401
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401
from test_perimeter import World, sql, world  # noqa: F401
from vds_sdk import Event, PermissionDenied
from vds_sdk.server.screens import ScreenCommandRequest
from vds_sdk.wire import SUBPROTOCOL_PLAYER
from vds_server.core.facade import ModuleCore
from vds_server.core.runtime import Runtime
from vds_server.ws import player as player_ws

CAPS = [
    "cmd.reload",
    "cmd.identify",
    "cmd.screenshot",
    "cmd.restart.app",
    "cmd.restart.device",
    "cmd.cache.resync",
]


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_ws, "CHECK_S", 0.2)


class CWorld:
    ids: dict[str, str]
    screens: dict[str, str]  # sA, sA1 (groupe A1), sB, sBare (sans appareil)
    tokens: dict[str, str]
    clients: dict[str, AsyncClient]
    player: AsyncClient
    app: FastAPI
    p: str


async def beat(c: CWorld, key: str, **extra: Any) -> dict[str, Any]:
    body = {"app_version": "1.11.0", "capabilities": CAPS, **extra}
    url = f"{c.p}/api/v1/player/heartbeat"
    r = await c.player.post(url, json=body, headers=hdr(c.tokens[key]))
    assert r.status_code == 200, r.text
    return dict(r.json())


@pytest.fixture
async def cw(world: World, admin: AsyncClient, app: FastAPI, p: str) -> CWorld:
    w = CWorld()
    w.ids, w.clients, w.app, w.p = world.ids, world.clients, app, p
    w.screens, w.tokens = {}, {}

    async def mk(key: str, site: str, group: str | None, name: str) -> None:
        body: dict[str, Any] = {"site_id": world.ids[site], "name": name}
        if group:
            body["group_id"] = world.ids[group]
        r = await admin.post(f"{p}/api/v1/screens", json=body, headers=SAME)
        assert r.status_code == 201, r.text
        w.screens[key] = str(r.json()["id"])

    await mk("sA", "A", None, "Ecran-ZZA")
    await mk("sA1", "A", "A1", "Ecran-ZZA1")
    await mk("sB", "B", None, "Ecran-ZZB")
    await mk("sBare", "A", None, "Ecran-ZZnu")
    w.player = new_client(app)
    for key in ("sA", "sA1", "sB"):
        w.tokens[key] = await pair(admin, w.player, p, w.screens[key])
        await beat(w, key)
    return w


def new_key() -> str:
    return uuid4().hex


async def send(
    c: AsyncClient, p: str, screen: str, name: str, args: dict[str, Any] | None = None, **kw: Any
) -> Any:
    body = {"name": name, "args": args or {}, "request_key": new_key(), **kw}
    return await c.post(f"{p}/api/v1/screens/{screen}/commands", json=body, headers=SAME)


async def get_cmd(admin: AsyncClient, p: str, cmd_id: str) -> dict[str, Any]:
    r = await admin.get(f"{p}/api/v1/player-commands", params={"ids": cmd_id})
    assert r.status_code == 200, r.text
    return dict(r.json()[0])


async def test_http_cycle_idempotence_and_double_delivery(
    cw: CWorld, admin: AsyncClient, p: str
) -> None:
    screen = cw.screens["sA"]
    body = {"name": "reload", "args": {}, "request_key": "key-0123456789"}
    r = await admin.post(f"{p}/api/v1/screens/{screen}/commands", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    cmd = r.json()
    assert cmd["status"] == "queued" and cmd["requested_by"] == "Root"
    r2 = await admin.post(f"{p}/api/v1/screens/{screen}/commands", json=body, headers=SAME)
    assert r2.status_code == 200 and r2.json()["id"] == cmd["id"]  # rejeu : même commande
    other = {**body, "name": "identify"}  # même clé, autre commande
    r3 = await admin.post(f"{p}/api/v1/screens/{screen}/commands", json=other, headers=SAME)
    assert (r3.status_code, r3.json()["code"]) == (409, "cmd.request_key_reused")
    # repli HTTP : livrée dans le heartbeat, au format {cmd_id, name, args, expires_at}
    ack = await beat(cw, "sA")
    assert [c["cmd_id"] for c in ack["commands"]] == [cmd["id"]]
    assert set(ack["commands"][0]) == {"cmd_id", "name", "args", "expires_at"}
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "sent"
    again = await beat(cw, "sA")  # tant que non terminale, redonnée (le player dédoublonne)
    assert [c["cmd_id"] for c in again["commands"]] == [cmd["id"]]
    res = {"cmd_id": cmd["id"], "status": "ok"}
    done = await beat(cw, "sA", command_results=[res])
    assert done["results_acked"] == [cmd["id"]] and done["commands"] == []
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "ok"
    replay = await beat(cw, "sA", command_results=[{**res, "status": "failed"}])
    assert replay["results_acked"] == [cmd["id"]]  # déjà terminal : ignoré, acquitté
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "ok"
    listing = await admin.get(f"{p}/api/v1/screens/{screen}/commands")
    assert [c["id"] for c in listing.json()] == [cmd["id"]]
    scr = (await admin.get(f"{p}/api/v1/screens/{screen}")).json()
    assert scr["capabilities"] == CAPS


async def test_heartbeat_fallback_caps_at_10_oldest_first(
    cw: CWorld, admin: AsyncClient, p: str
) -> None:
    screen = cw.screens["sA"]
    ids = [(await send(admin, p, screen, "reload")).json()["id"] for _ in range(12)]
    first = await beat(cw, "sA")
    assert [c["cmd_id"] for c in first["commands"]] == ids[:10]
    res = [{"cmd_id": i, "status": "ok"} for i in ids[:10]]
    second = await beat(cw, "sA", command_results=res)
    assert [c["cmd_id"] for c in second["commands"]] == ids[10:]


async def test_foreign_device_cannot_complete(cw: CWorld, admin: AsyncClient, p: str) -> None:
    cmd = (await send(admin, p, cw.screens["sA"], "reload")).json()
    ack = await beat(cw, "sB", command_results=[{"cmd_id": cmd["id"], "status": "ok"}])
    assert ack["commands"] == []
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "queued"


async def test_failure_audited_ok_not(
    cw: CWorld, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    a = (await send(admin, p, cw.screens["sA"], "reload")).json()
    b = (await send(admin, p, cw.screens["sA"], "identify", {"seconds": 5})).json()
    assert b["args"] == {"seconds": 5}
    results = [
        {"cmd_id": a["id"], "status": "ok"},
        {"cmd_id": b["id"], "status": "rejected", "code": "cmd.unsupported"},
    ]
    await beat(cw, "sA", command_results=results)
    got = await get_cmd(admin, p, b["id"])
    assert (got["status"], got["code"]) == ("rejected", "cmd.unsupported")
    rows = await sql(
        migrated_db_url, "SELECT action FROM audit_log WHERE action LIKE 'screen.command.%'"
    )
    actions = [r[0] for r in rows]
    assert actions.count("screen.command.requested") == 2
    assert actions.count("screen.command.failed") == 1
    assert "screen.command.cancelled" not in actions


async def test_events_published(cw: CWorld, admin: AsyncClient, p: str) -> None:
    rt: Runtime = cw.app.state.runtime
    seen: list[Event] = []

    async def on(event: Event) -> None:
        seen.append(event)

    rt.bus.subscribe("player.command_requested", on)
    rt.bus.subscribe("player.command_completed", on)
    batch = str(uuid4())
    cmd = (await send(admin, p, cw.screens["sA"], "reload", batch_id=batch)).json()
    await beat(cw, "sA", command_results=[{"cmd_id": cmd["id"], "status": "failed", "code": "x"}])
    by = {e.name: e.payload for e in seen}
    assert by["player.command_requested"]["batch_id"] == batch
    assert by["player.command_requested"]["screen_id"] == cw.screens["sA"]
    assert by["player.command_completed"]["status"] == "failed"
    assert by["player.command_completed"]["code"] == "x"


async def test_expiry_server_side(
    cw: CWorld, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    cmd = (await send(admin, p, cw.screens["sA"], "reload", ttl_s=30)).json()
    await sql(
        migrated_db_url,
        "UPDATE player_command SET expires_at = now() - interval '1 second' WHERE id = :i",
        i=UUID(cmd["id"]),
    )
    assert (await beat(cw, "sA"))["commands"] == []  # plus livrée, même avant le balayage
    rt: Runtime = cw.app.state.runtime
    assert await rt.capture_sweeper.expire() == 1
    got = await get_cmd(admin, p, cmd["id"])
    assert (got["status"], got["code"]) == ("expired", "cmd.expired")
    rows = await sql(
        migrated_db_url, "SELECT 1 FROM audit_log WHERE action='screen.command.failed'"
    )
    assert len(rows) == 1


async def test_cancel_only_from_queued(cw: CWorld, admin: AsyncClient, p: str) -> None:
    screen = cw.screens["sA"]
    c1 = (await send(admin, p, screen, "reload")).json()
    url = f"{p}/api/v1/screens/{screen}/commands/{c1['id']}/cancel"
    r = await admin.post(url, headers=SAME)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = await admin.post(url, headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "cmd.not_cancellable")
    assert (await beat(cw, "sA"))["commands"] == []
    c2 = (await send(admin, p, screen, "reload")).json()
    await beat(cw, "sA")  # envoyée
    r = await admin.post(f"{p}/api/v1/screens/{screen}/commands/{c2['id']}/cancel", headers=SAME)
    assert r.status_code == 409
    ghost = f"{p}/api/v1/screens/{screen}/commands/{uuid4()}/cancel"
    assert (await admin.post(ghost, headers=SAME)).status_code == 404


async def test_limits_validation_and_capabilities(cw: CWorld, admin: AsyncClient, p: str) -> None:
    screen = cw.screens["sA"]

    async def code(name: str, args: dict[str, Any] | None = None, **kw: Any) -> tuple[int, str]:
        r = await send(admin, p, screen, name, args, **kw)
        return r.status_code, str(r.json().get("code", "ok"))

    assert await code("restart") == (422, "cmd.args_invalid")
    assert await code("restart", {"scope": "wat"}) == (422, "cmd.args_invalid")
    assert await code("identify", {"seconds": 99}) == (422, "cmd.args_invalid")
    assert await code("identify", ttl_s=601) == (422, "cmd.ttl_invalid")
    assert (await send(admin, p, screen, "nope")).status_code == 422
    # plafond : 20 en attente, la 21e est refusée ; la capture est dédoublonnée
    shot = await send(admin, p, screen, "screenshot")
    again = await send(admin, p, screen, "screenshot")
    assert shot.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == shot.json()["id"]
    for _ in range(19):
        assert (await send(admin, p, screen, "reload")).status_code == 201
    r = await send(admin, p, screen, "reload")
    assert (r.status_code, r.json()["code"]) == (429, "cmd.too_many_pending")
    assert r.headers["Retry-After"]
    # capacités : vieux player (aucune annoncée) = reload seul ; écran sans appareil
    old = cw.screens["sB"]
    await beat(cw, "sB", capabilities=None)  # sans capacités annoncées
    assert (await send(admin, p, old, "reload")).status_code == 201
    r = await send(admin, p, old, "identify")
    assert (r.status_code, r.json()["code"]) == (409, "cmd.unsupported")
    r = await send(admin, p, cw.screens["sBare"], "reload")
    assert (r.status_code, r.json()["code"]) == (409, "cmd.no_device")


async def test_device_change_expires_pending(cw: CWorld, admin: AsyncClient, p: str) -> None:
    screen = cw.screens["sA"]
    cmd = (await send(admin, p, screen, "reload")).json()
    r = await admin.delete(f"{p}/api/v1/screens/{screen}/device", headers=SAME)
    assert r.status_code == 204
    got = await get_cmd(admin, p, cmd["id"])
    assert (got["status"], got["code"]) == ("expired", "cmd.device_changed")


async def test_screen_delete_expires_pending(cw: CWorld, admin: AsyncClient, p: str) -> None:
    screen = cw.screens["sA"]
    cmd = (await send(admin, p, screen, "reload")).json()
    assert (await admin.delete(f"{p}/api/v1/screens/{screen}", headers=SAME)).status_code == 204
    got = await get_cmd(admin, p, cmd["id"])
    assert (got["status"], got["code"], got["screen_id"]) == ("expired", "screen.deleted", None)


# --- droits (lot J) ----------------------------------------------------------------------
async def test_perimeter_and_roles(cw: CWorld, admin: AsyncClient, p: str) -> None:
    ed_a, ed_a1, ed_none = cw.clients["ed_A"], cw.clients["ed_A1"], cw.clients["ed_none"]
    sa, sa1, sb = cw.screens["sA"], cw.screens["sA1"], cw.screens["sB"]
    # éditeur : recharger / identifier / capturer dans son périmètre
    for name, args in (("reload", {}), ("identify", {"seconds": 5}), ("screenshot", {})):
        r = await send(ed_a, p, sa, name, args)
        assert r.status_code == 201, (name, r.text)
        assert r.json()["requested_by"] == "ed_A"
    assert (await send(ed_a, p, sa1, "reload")).status_code == 201  # écran d'un groupe du site
    # restart / cache.resync : admin seul (403 explicite dans le périmètre)
    for name2, args2 in (("restart", {"scope": "app"}), ("cache.resync", {})):
        r = await send(ed_a, p, sa, name2, args2)
        assert (r.status_code, r.json()["code"]) == (403, "cmd.role_required")
        assert (await send(admin, p, sa, name2, args2)).status_code == 201
    # hors périmètre : 404 sans nom, y compris pour une commande réservée à l'admin
    for who, targets in ((ed_a, (sb,)), (ed_none, (sa, sb))):
        for screen in targets:
            for name3, args3 in (("reload", {}), ("restart", {"scope": "app"})):
                r = await send(who, p, screen, name3, args3)
                assert r.status_code == 404 and "ZZ" not in r.text, (name3, r.text)
    assert (await send(ed_a1, p, sa, "reload")).status_code == 404  # groupe : pas l'écran du site
    assert (await send(ed_a1, p, sa1, "reload")).status_code == 201
    # lecture : 404 ; sondage groupé omet les ids hors périmètre
    cb = (await send(admin, p, sb, "reload")).json()
    ca = (await send(admin, p, sa, "reload")).json()
    assert (await ed_a.get(f"{p}/api/v1/screens/{sb}/commands")).status_code == 404
    assert (await ed_none.get(f"{p}/api/v1/screens/{sa}/commands")).status_code == 404
    ids = {"ids": f"{cb['id']},{ca['id']}"}
    r = await ed_a.get(f"{p}/api/v1/player-commands", params=ids)
    assert [c["id"] for c in r.json()] == [ca["id"]] and "ZZ" not in r.text
    r = await ed_none.get(f"{p}/api/v1/player-commands", params=ids)
    assert r.status_code == 200 and r.json() == []
    r = await admin.get(f"{p}/api/v1/player-commands", params=ids)
    assert len(r.json()) == 2
    for bad in ("zzz", ",".join(str(uuid4()) for _ in range(201))):
        r = await admin.get(f"{p}/api/v1/player-commands", params={"ids": bad})
        assert (r.status_code, r.json()["code"]) == (422, "cmd.ids_invalid"), bad
    # annulation : hors périmètre 404 ; restart annulable par l'admin seul
    r = await ed_a.post(f"{p}/api/v1/screens/{sb}/commands/{cb['id']}/cancel", headers=SAME)
    assert r.status_code == 404
    rs = (await send(admin, p, sa1, "restart", {"scope": "app"})).json()
    r = await ed_a.post(f"{p}/api/v1/screens/{sa1}/commands/{rs['id']}/cancel", headers=SAME)
    assert (r.status_code, r.json()["code"]) == (403, "cmd.role_required")
    r = await ed_a.post(f"{p}/api/v1/screens/{sa}/commands/{rs['id']}/cancel", headers=SAME)
    assert r.status_code == 404  # commande d'un autre écran : jamais de fuite d'existence


async def test_writes_need_session(cw: CWorld, p: str) -> None:
    c = new_client(cw.app)
    body = {"name": "reload", "request_key": new_key()}
    url = f"{p}/api/v1/screens/{cw.screens['sA']}/commands"
    assert (await c.post(url, json=body, headers=SAME)).status_code == 401


# --- modules (ScreensApi) --------------------------------------------------------------------
async def test_module_screens_api(cw: CWorld, admin: AsyncClient, p: str) -> None:
    rt: Runtime = cw.app.state.runtime

    def core(perms: list[str]) -> ModuleCore:
        manifest: Any = SimpleNamespace(id="notify.test", permissions=perms)
        return ModuleCore(rt, manifest)

    with pytest.raises(PermissionDenied):
        core([]).screens  # noqa: B018
    api = core(["screens.command"]).screens
    req = ScreenCommandRequest(screen_id=UUID(cw.screens["sA"]), name="reload")
    cmd_id = await api.send_command(req)
    got = await get_cmd(admin, p, str(cmd_id))
    assert got["status"] == "queued" and got["requested_by"] is None
    with pytest.raises(Exception, match="screen.not_found"):
        await api.send_command(ScreenCommandRequest(screen_id=uuid4(), name="reload"))


# --- livraison WebSocket ---------------------------------------------------------------------
async def open_authed(c: CWorld, key: str) -> FakeWS:
    ws = await FakeWS(c.app, f"{c.p}/ws/player", {}, [SUBPROTOCOL_PLAYER]).open()
    await ws.send("auth", {"token": c.tokens[key]})
    w = await ws.recv()
    assert w is not None and w["type"] == "welcome"
    return ws


async def test_ws_push_result_and_replay_on_reconnect(
    cw: CWorld, admin: AsyncClient, p: str
) -> None:
    screen = cw.screens["sA"]
    ws = await open_authed(cw, "sA")
    cmd = (await send(admin, p, screen, "identify", {"seconds": 7})).json()
    f = await ws.recv()
    assert f is not None and f["type"] == "cmd"
    assert f["data"]["cmd_id"] == cmd["id"] and f["data"]["name"] == "identify"
    assert f["data"]["args"] == {"seconds": 7} and f["data"]["expires_at"] and f["seq"] == 1
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "sent"
    await ws.send("cmd.result", {"cmd_id": cmd["id"], "status": "ok"}, seq=1)
    ack = await ws.recv()
    assert ack is not None and ack["type"] == "ack" and ack["ack"] == 1
    for _ in range(20):
        if (await get_cmd(admin, p, cmd["id"]))["status"] == "ok":
            break
        await asyncio.sleep(0.1)
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "ok"
    # non acquittée : repoussée à la reconnexion (après welcome)
    pending = (await send(admin, p, screen, "reload")).json()
    f = await ws.recv()
    assert f is not None and f["data"]["cmd_id"] == pending["id"]
    await ws.close()
    ws2 = await open_authed(cw, "sA")
    f = await ws2.recv()
    assert f is not None and f["type"] == "cmd" and f["data"]["cmd_id"] == pending["id"]
    # un résultat d'une commande inconnue ne casse rien
    await ws2.send("cmd.result", {"cmd_id": str(uuid4()), "status": "ok"}, seq=1)
    ack2 = await ws2.recv()
    assert ack2 is not None and ack2["type"] == "ack"
    await ws2.close()


async def test_ws_heartbeat_updates_capabilities(cw: CWorld, admin: AsyncClient, p: str) -> None:
    ws = await open_authed(cw, "sB")
    await ws.send("heartbeat", {"capabilities": ["cmd.reload", "cmd.identify"]})
    for _ in range(20):
        r = await send(admin, p, cw.screens["sB"], "screenshot")
        if r.status_code == 409:
            break
        await asyncio.sleep(0.1)
    assert r.status_code == 409 and r.json()["code"] == "cmd.unsupported"
    assert (await send(admin, p, cw.screens["sB"], "identify")).status_code == 201
    await ws.close()


def test_scope_routes_classified() -> None:
    from vds_server.core.scope_routes import SCOPE_ROUTES

    for route in (
        "POST /api/v1/screens/{screen_id}/commands",
        "GET /api/v1/screens/{screen_id}/commands",
        "GET /api/v1/player-commands",
        "POST /api/v1/screens/{screen_id}/commands/{command_id}/cancel",
        "GET /api/v1/screens/{screen_id}/screenshots",
        "GET /api/v1/screenshots/{screenshot_id}/image",
    ):
        assert SCOPE_ROUTES[route] == "scoped"
    assert SCOPE_ROUTES["POST /api/v1/player/screenshots"] == "scope_neutral"
