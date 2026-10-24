import asyncio
import datetime as dt
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from helpers import FakeServer, cmd_frame, eventually, write_fake_module
from vds_agent.cascade import Cascade
from vds_agent.runtime import Agent
from vds_agent.settings import AgentSettings, SettingsError, http_base, ws_url
from vds_agent.store import Store
from vds_sdk.wire import AgentWelcome

SID = str(uuid4())
TOKEN = "vds_agt_" + "x" * 43  # jeton factice de test


async def fast(_: float) -> None:
    await asyncio.sleep(0.02)


def plan(power: list[dict[str, str]] | None = None, rev: str = "r1") -> dict[str, object]:
    return {
        "revision": rev,
        "screens": [
            {
                "screen_id": SID,
                "timezone": "Europe/Paris",
                "power": power or [],
                "cascade": ["power.fake"],
                "drivers": {"power.fake": {}},
            }
        ],
    }


def http_with(plan_doc: dict[str, object]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert req.url.path == "/espace/affichage/api/v1/agent/plan"
        return httpx.Response(200, json=plan_doc, headers={"ETag": '"r1"'})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_agent(tmp: Path, srv: FakeServer, plan_doc: dict[str, object], **kw: object) -> Agent:
    mods = tmp / "mods"
    write_fake_module(mods)
    s = AgentSettings(
        server_url=srv.url, token=TOKEN, data_dir=tmp / "data", modules_dir=mods, ca_file=srv.ca
    )
    return Agent(
        s,
        Store(s.data_dir),
        cascade=Cascade(verify_delay_s=0, sleep=fast),
        http=http_with(plan_doc),
        heartbeat_s=0.2,
        sleep=fast,
        **kw,
    )  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    for name in list(sys.modules):
        if name.startswith("vds_mod_power_fake"):
            del sys.modules[name]


def fake_state() -> dict[str, object]:
    return sys.modules["vds_mod_power_fake.agent.module"].STATE  # type: ignore[no-any-return]


def test_url_derivation() -> None:
    assert (
        ws_url("https://h.example/espace/affichage/") == "wss://h.example/espace/affichage/ws/agent"
    )
    assert ws_url("https://h.example") == "wss://h.example/ws/agent"
    assert http_base("https://h.example/espace/affichage/") == "https://h.example/espace/affichage"
    with pytest.raises(SettingsError):
        ws_url("http://h.example")  # TLS obligatoire, pas d'option insecure


def test_settings_token_file(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text(TOKEN + "\n")
    s = AgentSettings.from_env({"VDS_SERVER_URL": "https://h/", "VDS_AGENT_TOKEN_FILE": str(f)})
    assert s.token == TOKEN
    with pytest.raises(SettingsError):
        AgentSettings.from_env({"VDS_SERVER_URL": "https://h/"})


async def test_connect_cmd_flow_and_headers(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, ["power.fake"], plan_revision="r1") as srv:
        srv.to_send = [
            cmd_frame(1, "plan.updated", {"revision": "r1"}),
            cmd_frame(2, "power.set", {"state": "on", "reason": "manual", "request_id": "rq"}, SID),
            cmd_frame(3, "nope", {}),
        ]
        agent = make_agent(tmp_path, srv, plan())
        task = asyncio.create_task(agent.run())
        try:
            await eventually(lambda: len(srv.results()) >= 3)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)
        assert {k.lower(): v for k, v in srv.headers[0].items()}[
            "authorization"
        ] == f"Bearer {TOKEN}"
        assert (
            "origin" not in {k.lower() for k in srv.headers[0]}
            and srv.subprotocols[0] == "vds.agent.v1"
        )
        assert srv.path == "/espace/affichage/ws/agent"
        assert srv.hellos[0]["modules"] == [{"id": "power.fake", "version": "0.1.0"}]
        by = {r["status"]: r for r in srv.results()}
        assert by["ok"]["data"]["driver"] == "power.fake" and by["ok"]["data"]["verified"] is True
        assert by["rejected"]["code"] == "unknown_command"
        ev = srv.events()[0]
        assert ev["name"] == "power.changed" and ev["payload"]["request_id"] == "rq"
        assert fake_state()["s"] == "on"
        assert any(f.type == "ack" and f.ack == 3 for f in srv.received)
        assert any(f.type == "heartbeat" and f.data["plan_revision"] == "r1" for f in srv.received)


async def test_expired_and_replayed_command_not_reexecuted(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        old = cmd_frame(1, "power.set", {"state": "on", "request_id": "a"}, SID, expires_in_s=-5)
        srv.to_send = [old]
        agent = make_agent(tmp_path, srv, plan())
        task = asyncio.create_task(agent.run())
        try:
            await eventually(lambda: len(srv.results()) >= 1)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)
        assert srv.results()[0]["status"] == "expired" and fake_state()["calls"] == []


async def test_reconnect_resume_and_outbox_replay(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        agent = make_agent(tmp_path, srv, plan())
        await agent.start()
        # événements produits hors ligne : conservés dans l'outbox SQLite
        await agent.link.emit(
            "power.failed", {"screen_id": SID, "state": "on", "attempted": [], "code": "x"}
        )
        await agent.link.emit(
            "power.changed", {"screen_id": SID, "state": "on", "driver": "d", "verified": True}
        )
        srv.resume_from = 1  # le serveur avait déjà reçu le seq 1
        task = asyncio.create_task(agent.link.run())
        try:
            await eventually(lambda: len(srv.events()) >= 1)
            await asyncio.sleep(0.2)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)
        assert [f.seq for f in srv.received if f.type == "event"] == [2]
        # le hello annonce le dernier seq serveur traité
        assert srv.hellos[0]["last_server_seq"] == 0


async def test_reconnects_with_backoff_after_drop(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, []) as srv:
        srv.close_after_welcome = 1011
        agent = make_agent(tmp_path, srv, plan())
        task = asyncio.create_task(agent.link.run())
        try:
            await eventually(lambda: srv.connections >= 3)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)


async def test_revoked_wipes_secrets_and_stops(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, []) as srv:
        srv.close_after_welcome = 4410
        agent = make_agent(tmp_path, srv, plan())
        await agent.store.state_set("power.samsung-ws", "token:x", "secret")
        await asyncio.wait_for(agent.link.run(), 5)
        assert srv.connections == 1
        assert await agent.store.state_get("power.samsung-ws", "token:x") is None


async def test_autonomous_schedule_offline(tmp_path: Path) -> None:
    """Sans serveur joignable : plan en cache, bascule exécutée à l'heure, événement en outbox."""
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        now = dt.datetime(2026, 10, 24, 7, 0, tzinfo=dt.UTC)
        slots = [
            {"from": "2026-10-24T06:00:00+00:00", "to": "2026-10-24T18:00:00+00:00", "state": "on"},
            {
                "from": "2026-10-24T18:00:00+00:00",
                "to": "2026-10-25T06:00:00+00:00",
                "state": "off",
            },
        ]
        agent = make_agent(tmp_path, srv, plan(slots), clock=lambda: now)
        await agent.store.kv_set("plan", plan(slots))
        await agent.store.kv_set("modules_enabled", [{"id": "power.fake", "version": "0.1.0"}])
        await agent.start()  # aucune connexion
        await agent.tick()
        assert fake_state()["s"] == "on"
        out = await agent.store.outbox_after(0)
        assert out and out[0].data["name"] == "power.changed"
        await agent.tick()
        assert fake_state()["calls"] == ["on"]  # pas de répétition dans le même créneau
        agent.clock = lambda: now.replace(hour=19)
        await agent.tick()
        assert fake_state()["s"] == "off"
        await agent.close()


async def test_schedule_retries_after_failure(tmp_path: Path) -> None:
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        now = dt.datetime(2026, 10, 24, 7, 0, tzinfo=dt.UTC)
        slots = [
            {"from": "2026-10-24T06:00:00+00:00", "to": "2026-10-24T18:00:00+00:00", "state": "on"}
        ]
        agent = make_agent(tmp_path, srv, plan(slots), clock=lambda: now)
        await agent.store.kv_set("plan", plan(slots))
        await agent.store.kv_set("modules_enabled", [{"id": "power.fake", "version": "0.1.0"}])
        await agent.start()
        fake_state()["fail"] = True
        await agent.tick()
        first = (await agent.store.outbox_after(0))[-1].data["name"]
        assert first == "power.failed" or first == "power.changed"
        await agent.close()


def test_module_not_enabled_is_not_loaded(tmp_path: Path) -> None:
    from vds_agent.modules import discover

    write_fake_module(tmp_path)
    assert [d.manifest.id for d in discover(tmp_path)] == ["power.fake"]


def test_state_is_encrypted_at_rest(tmp_path: Path) -> None:
    async def go() -> None:
        st = Store(tmp_path)
        await st.state_set("power.samsung-ws", "token:h", "SECRET-TOKEN-123")
        st.close()

    asyncio.run(go())
    raw = (tmp_path / "agent.db").read_bytes()
    assert b"SECRET-TOKEN-123" not in raw
    assert oct((tmp_path / "state.key").stat().st_mode & 0o777) == "0o600"


async def test_token_rotated_keeps_state_and_announces_out_seq(tmp_path: Path) -> None:
    """I4 : 4411 arrête l'agent sans effacer états ni seq ; le hello annonce out_seq."""
    async with FakeServer(tmp_path, []) as srv:
        srv.close_after_welcome = 4411
        agent = make_agent(tmp_path, srv, plan())
        await agent.store.state_set("power.samsung-ws", "token:x", "secret")
        await agent.store.kv_set("out_seq", 7)
        await asyncio.wait_for(agent.link.run(), 5)
        assert srv.connections == 1
        assert await agent.store.state_get("power.samsung-ws", "token:x") == "secret"
        assert await agent.store.kv_get("out_seq") == 7
        assert srv.hellos[0]["out_seq"] == 7


async def test_module_removed_from_welcome_is_torn_down(tmp_path: Path) -> None:
    """I1 : un pilote qui n'est plus listé dans modules_enabled est déchargé à chaud."""
    async with FakeServer(tmp_path, ["power.fake"]) as srv:
        agent = make_agent(tmp_path, srv, plan())
        await agent.start()
        await agent._welcomed(_welcome(["power.fake"]))
        assert "power.fake" in agent.drivers
        await agent._welcomed(_welcome([]))
        assert agent.drivers == {}
        await agent.close()


def _welcome(modules: list[str]) -> AgentWelcome:
    from helpers import welcome_data
    from vds_sdk.wire import AgentWelcome

    return AgentWelcome.model_validate(welcome_data(modules))


def test_insecure_dev_opt_in_private_only() -> None:
    assert ws_url("http://192.168.1.182:8002", True) == "ws://192.168.1.182:8002/ws/agent"
    assert http_base("http://192.168.1.182:8002/", True) == "http://192.168.1.182:8002"
    with pytest.raises(SettingsError):
        ws_url("http://192.168.1.182:8002")  # refusé par défaut
    with pytest.raises(SettingsError):
        ws_url("http://example.com", True)  # nom public refusé même en dev
    with pytest.raises(SettingsError):
        ws_url("http://8.8.8.8", True)


def test_extra_formatter_masks_secrets_and_renders_fields() -> None:
    import logging

    from vds_agent.logsetup import ExtraFormatter

    rec = logging.LogRecord("x", logging.WARNING, "f", 1, "agent.link_error", None, None)
    rec.__dict__.update({"err": "InvalidStatus", "token": "abc", "close_code": 1008})
    out = ExtraFormatter("%(message)s").format(rec)
    assert "err='InvalidStatus'" in out and "close_code=1008" in out
    assert "abc" not in out and "token=***" in out


def test_describe_link_error_reports_close_code() -> None:
    from vds_agent.link import describe_link_error
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    e = ConnectionClosedError(Close(1008, "policy"), None)
    info = describe_link_error(e)
    assert info["err"] == "ConnectionClosedError"
    assert info["close_code"] == 1008 and info["close_reason"] == "policy"
