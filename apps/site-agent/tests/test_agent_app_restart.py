import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from helpers import FakeServer, cmd_frame, eventually
from vds_agent.app_restart import AppRestartRunner
from vds_agent.cascade import Cascade
from vds_agent.runtime import Agent
from vds_agent.settings import AgentSettings
from vds_agent.store import Store
from vds_sdk.agent import AppRestartOutcome, AppRestartRequest

TOKEN = "vds_agt_" + "z" * 43
SID = str(uuid4())


def args() -> dict[str, Any]:
    return {"request_id": str(uuid4()), "platform": "android"}


class FakeRestarter:
    platforms = frozenset({"android"})

    def __init__(self, slow: float = 0, out: AppRestartOutcome | None = None) -> None:
        self.slow = slow
        self.calls: list[AppRestartRequest] = []
        self.out = out or AppRestartOutcome(ok=True)

    async def restart_app(self, req: AppRestartRequest) -> AppRestartOutcome:
        self.calls.append(req)
        await asyncio.sleep(self.slow)
        return self.out


def runner(
    fake: FakeRestarter | None,
    locks: dict[str, asyncio.Lock] | None = None,
    known: bool = True,
    now: list[float] | None = None,
    timeout_s: float = 5,
) -> AppRestartRunner:
    locks = locks if locks is not None else {}
    t = now if now is not None else [0.0]
    return AppRestartRunner(
        restarters=lambda p: fake if p == "android" else None,
        screen_lock=lambda sid: locks.setdefault(sid, asyncio.Lock()),
        host_lock=lambda sid: locks.setdefault("host", asyncio.Lock()),
        prepare=lambda sid: known,
        timeout_s=timeout_s,
        clock=lambda: t[0],
    )


async def test_ok_and_screen_id_from_command() -> None:
    f = FakeRestarter()
    status, code, data = await runner(f).run(SID, args())
    assert (status, code, data["ok"]) == ("ok", None, True)
    assert str(f.calls[0].screen_id) == SID


async def test_failure_code_returned() -> None:
    f = FakeRestarter(out=AppRestartOutcome(ok=False, code="app.restart.not_installed"))
    status, code, _ = await runner(f).run(SID, args())
    assert (status, code) == ("failed", "app.restart.not_installed")


async def test_unsupported_and_unknown_screen_never_call_module() -> None:
    assert (await runner(None).run(SID, args()))[1] == "app.restart.unsupported"
    f = FakeRestarter()
    assert (await runner(f, known=False).run(SID, args()))[1] == "app.restart.unsupported"
    assert f.calls == []


async def test_busy_when_screen_or_host_locked() -> None:
    locks: dict[str, asyncio.Lock] = {}
    r = runner(FakeRestarter(), locks)
    async with locks.setdefault(SID, asyncio.Lock()):  # power.set en cours
        assert (await r.run(SID, args()))[1] == "app.restart.busy"
    async with locks.setdefault("host", asyncio.Lock()):  # apk.update sur le même hôte
        assert (await r.run(SID, args()))[1] == "app.restart.busy"


async def test_missing_key_fails_cleanly() -> None:
    f = FakeRestarter()
    r = runner(f)
    out = await r.run(SID, {"platform": "android"})  # request_id absent
    assert out[0] == "failed" and out[1] == "app.restart.unsupported" and f.calls == []


async def test_burst_limited_to_one_per_30s() -> None:
    now = [100.0]
    f = FakeRestarter()
    r = runner(f, now=now)
    assert (await r.run(SID, args()))[0] == "ok"
    now[0] = 120.0
    assert (await r.run(SID, args()))[1] == "app.restart.busy"
    assert (await r.run(str(uuid4()), args()))[0] == "ok"  # autre appareil : libre
    now[0] = 131.0
    assert (await r.run(SID, args()))[0] == "ok" and len(f.calls) == 3


async def test_timeout_and_exception_are_sanitized() -> None:
    assert (await runner(FakeRestarter(slow=2), timeout_s=0.1).run(SID, args()))[1] == (
        "app.restart.adb_unreachable"
    )

    class Boom(FakeRestarter):
        async def restart_app(self, req: AppRestartRequest) -> AppRestartOutcome:
            raise RuntimeError("shell: secret-output")

    _, code, data = await runner(Boom()).run(SID, args())
    assert code == "app.restart.launch_failed" and "secret" not in json.dumps(data)


MODULE = """
from typing import ClassVar
from vds_sdk.agent import AppRestartOutcome, PowerDriver, PowerResult

class RstDriver(PowerDriver):
    capabilities: ClassVar[frozenset] = frozenset({"power.on"})
    platforms: ClassVar[frozenset] = frozenset({"android"})
    async def set_power(self, target, state):
        return PowerResult(ok=True, state="unknown", verified=False)
    async def get_state(self, target):
        return "off"
    async def restart_app(self, req):
        return AppRestartOutcome(ok=True)
"""


async def test_agent_routes_app_restart_to_module(tmp_path: Path) -> None:
    for n in [k for k in sys.modules if k.startswith("vds_mod_power_rst")]:
        del sys.modules[n]
    d = tmp_path / "mods" / "power.rst"
    (d / "agent").mkdir(parents=True)
    (d / "agent" / "module.py").write_text(MODULE)
    (d / "module.json").write_text(
        json.dumps(
            {
                "id": "power.rst",
                "version": "0.1.0",
                "core": ">=1.14 <2.0",
                "kind": "power-driver",
                "name": {"fr": "P", "en": "P"},
                "description": {"fr": "P", "en": "P"},
                "targets": ["site-agent"],
                "entrypoints": {"site-agent": "agent.module:RstDriver"},
                "capabilities": ["power.on", "app.restart.android"],
                "permissions": ["screens.read", "screens.provision"],
                "events": {"listens": [], "emits": []},
                "author": "test",
            }
        )
    )
    async with FakeServer(tmp_path, ["power.rst"]) as srv:
        srv.to_send = [cmd_frame(1, "app.restart", {**args(), "screen_id": SID}, SID)]
        s = AgentSettings(
            server_url=srv.url,
            token=TOKEN,
            data_dir=tmp_path / "data",
            modules_dir=tmp_path / "mods",
            ca_file=srv.ca,
        )

        async def fast(_: float) -> None:
            await asyncio.sleep(0.02)

        agent = Agent(
            s,
            Store(s.data_dir),
            cascade=Cascade(verify_delay_s=0, sleep=fast),
            http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
            heartbeat_s=0.2,
            sleep=fast,
        )
        agent.screen_entry = lambda sid: {"screen_id": sid, "cascade": [], "drivers": {}}  # type: ignore[method-assign]
        task = asyncio.create_task(agent.run())
        try:
            await eventually(lambda: len(srv.results()) >= 1, timeout=30)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 15)
        res = srv.results()[0]
        assert res["status"] == "ok" and res["data"] == {"ok": True, "code": None}
