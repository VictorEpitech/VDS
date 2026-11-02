import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from helpers import FakeServer, cmd_frame, eventually
from vds_agent.apk_update import ApkUpdateRunner
from vds_agent.cascade import Cascade
from vds_agent.runtime import Agent
from vds_agent.settings import AgentSettings
from vds_agent.store import Store
from vds_sdk.agent import ApkSource, ApkUpdateOutcome, ApkUpdateRequest, StepReport

TOKEN = "vds_agt_" + "y" * 43
APK = b"fake-apk-bytes" * 100
SHA = hashlib.sha256(APK).hexdigest()
SID = str(uuid4())


def args(**kw: Any) -> dict[str, Any]:
    base = {
        "request_id": str(uuid4()),
        "platform": "android",
        "apk": {
            "package": "app.vds.player",
            "version_code": 7,
            "version_name": "1.0.7",
            "sha256": SHA,
            "size": len(APK),
        },
    }
    return {**base, **kw}


class FakeUpdater:
    platforms = frozenset({"android"})

    def __init__(self, slow: float = 0) -> None:
        self.slow = slow
        self.running = 0
        self.peak = 0
        self.paths: list[Path] = []
        self.screens: list[str] = []

    async def update(self, req: ApkUpdateRequest, apk: ApkSource, report: Any) -> ApkUpdateOutcome:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            self.screens.append(str(req.screen_id))
            await report(StepReport(step="download", status="running"))
            self.paths.append(await apk.fetch(req.apk))
            if self.slow:
                await asyncio.sleep(self.slow)
        finally:
            self.running -= 1
        return ApkUpdateOutcome(
            ok=True, previous_version_code=5, installed_version_code=req.apk.version_code
        )


def runner(
    tmp: Path,
    updater: FakeUpdater | None,
    locks: dict[str, asyncio.Lock] | None = None,
    known: bool = True,
    seen: list[httpx.Request] | None = None,
    status: int = 200,
    body: bytes = APK,
    timeout_s: float = 5,
    max_installs: int = 2,
) -> ApkUpdateRunner:
    locks = locks if locks is not None else {}

    async def slow_body(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        await asyncio.sleep(0.05)
        return httpx.Response(status, content=body)

    return ApkUpdateRunner(
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow_body)),
        http_base="https://vds.example/espace/affichage",
        token=TOKEN,
        data_dir=tmp,
        updaters=lambda p: updater if p == "android" else None,
        screen_lock=lambda sid: locks.setdefault(sid, asyncio.Lock()),
        prepare=lambda sid: known,
        timeout_s=timeout_s,
        max_installs=max_installs,
    )


async def test_ok_uses_agent_route_and_returns_versions(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    up = FakeUpdater()
    status, code, data = await runner(tmp_path, up, seen=seen).run(SID, args())
    assert (status, code) == ("ok", None)
    assert data["previous_version_code"] == 5 and data["installed_version_code"] == 7
    assert str(seen[0].url) == "https://vds.example/espace/affichage/api/v1/agent/apk/android/7"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert up.paths[0].read_bytes() == APK


async def test_two_screens_one_download(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    up = FakeUpdater(slow=0.05)
    r = runner(tmp_path, up, seen=seen)
    res = await asyncio.gather(r.run(SID, args()), r.run(str(uuid4()), args()))
    assert [x[0] for x in res] == ["ok", "ok"] and len(seen) == 1 and up.peak == 2


async def test_at_most_two_installs_per_site(tmp_path: Path) -> None:
    up = FakeUpdater(slow=0.1)
    r = runner(tmp_path, up)
    res = await asyncio.gather(*(r.run(str(uuid4()), args()) for _ in range(5)))
    assert all(x[0] == "ok" for x in res) and up.peak == 2


async def test_device_lock_busy_and_shared_with_power(tmp_path: Path) -> None:
    locks: dict[str, asyncio.Lock] = {}
    r = runner(tmp_path, FakeUpdater(slow=0.2), locks)
    first = asyncio.create_task(r.run(SID, args()))
    await asyncio.sleep(0.1)
    assert locks[SID].locked()  # le même verrou que `Agent.apply_power`
    assert (await r.run(SID, args()))[1] == "update.android.busy"
    assert (await first)[0] == "ok"
    async with locks[SID]:  # un power.set en cours => busy
        assert (await r.run(SID, args()))[1] == "update.android.busy"


async def test_unknown_screen_no_provider_timeout(tmp_path: Path) -> None:
    assert (await runner(tmp_path, FakeUpdater(), known=False).run(SID, args()))[1] == (
        "update.unknown_screen"
    )
    assert (await runner(tmp_path, None).run(SID, args()))[1] == "update.no_provider"
    t = runner(tmp_path, FakeUpdater(slow=2), timeout_s=0.1)
    assert (await t.run(SID, args()))[1] == "update.timeout"


async def test_download_errors_use_update_prefix(tmp_path: Path) -> None:
    r = runner(tmp_path, FakeUpdater(), status=404)
    assert (await r.run(SID, args()))[1] == "update.android.apk_unavailable"
    r = runner(tmp_path, FakeUpdater(), body=b"x" * len(APK))
    assert (await r.run(SID, args()))[1] == "update.android.apk_mismatch"
    assert not list((tmp_path / "apk").glob("*.apk"))


async def test_args_carry_no_address_or_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        from vds_sdk.wire.messages import ApkUpdateArgs

        ApkUpdateArgs.model_validate({**args(), "address": "192.168.1.9"})
    with pytest.raises(ValueError):
        from vds_sdk.wire.messages import ApkUpdateArgs

        ApkUpdateArgs.model_validate({**args(), "url": "http://x/a.apk"})


async def test_module_exception_is_sanitized(tmp_path: Path) -> None:
    class Boom(FakeUpdater):
        async def update(self, *a: Any, **k: Any) -> ApkUpdateOutcome:
            raise RuntimeError("shell: secret-output")

    status, code, data = await runner(tmp_path, Boom()).run(SID, args())
    assert code == "update.module_error" and "secret" not in json.dumps(data)


UPD_MODULE = """
from typing import ClassVar
from vds_sdk.agent import ApkUpdateOutcome, PowerDriver, PowerResult

class UpdDriver(PowerDriver):
    capabilities: ClassVar[frozenset] = frozenset({"power.on"})
    platforms: ClassVar[frozenset] = frozenset({"android"})
    async def set_power(self, target, state):
        return PowerResult(ok=True, state="unknown", verified=False)
    async def get_state(self, target):
        return "off"
    async def update(self, req, apk, report):
        await apk.fetch(req.apk)
        return ApkUpdateOutcome(ok=True, previous_version_code=1,
                                installed_version_code=req.apk.version_code)
"""


async def test_agent_routes_apk_update_to_module(tmp_path: Path) -> None:
    for n in [k for k in sys.modules if k.startswith("vds_mod_power_upd")]:
        del sys.modules[n]
    d = tmp_path / "mods" / "power.upd"
    (d / "agent").mkdir(parents=True)
    (d / "agent" / "module.py").write_text(UPD_MODULE)
    (d / "module.json").write_text(
        json.dumps(
            {
                "id": "power.upd",
                "version": "0.1.0",
                "core": ">=1.6 <2.0",
                "kind": "power-driver",
                "name": {"fr": "P", "en": "P"},
                "description": {"fr": "P", "en": "P"},
                "targets": ["site-agent"],
                "entrypoints": {"site-agent": "agent.module:UpdDriver"},
                "capabilities": ["power.on", "update.android"],
                "permissions": ["screens.provision"],
                "events": {"listens": [], "emits": []},
                "author": "test",
            }
        )
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=APK)

    async with FakeServer(tmp_path, ["power.upd"]) as srv:
        srv.to_send = [cmd_frame(1, "apk.update", {**args(), "screen_id": SID}, SID)]
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
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            heartbeat_s=0.2,
            sleep=fast,
        )
        agent.screen_entry = lambda sid: {"screen_id": sid, "cascade": [], "drivers": {}}  # type: ignore[method-assign]
        task = asyncio.create_task(agent.run())
        try:
            # marge large : la charge CI (TLS, import du module) peut dépasser 5 s
            await eventually(lambda: len(srv.results()) >= 1, timeout=30)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 15)
        res = srv.results()[0]
        assert res["status"] == "ok" and res["data"]["installed_version_code"] == 7


async def test_same_host_serialized(tmp_path: Path) -> None:
    hl: dict[str, asyncio.Lock] = {}
    up = FakeUpdater(slow=0.05)
    r = runner(tmp_path, up)
    r._host_lock = lambda sid: hl.setdefault("h", asyncio.Lock())  # noqa: SLF001
    res = await asyncio.gather(r.run(SID, args()), r.run(str(uuid4()), args()))
    assert [x[0] for x in res] == ["ok", "ok"] and up.peak == 1
