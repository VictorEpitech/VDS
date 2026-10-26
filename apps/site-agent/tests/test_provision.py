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
from vds_agent.cascade import Cascade
from vds_agent.provision import ProvisionRunner
from vds_agent.runtime import Agent
from vds_agent.settings import AgentSettings
from vds_agent.store import Store
from vds_sdk.agent import ApkSource, ProvisionOutcome, ProvisionRequest, StepReport

TOKEN = "vds_agt_" + "y" * 43
TICKET = "A" * 32
APK = b"fake-apk-bytes" * 100
SHA = hashlib.sha256(APK).hexdigest()
SID = str(uuid4())


def args(**kw: Any) -> dict[str, Any]:
    base = {
        "provision_id": str(uuid4()),
        "platform": "android",
        "address": "192.168.1.50",
        "apk": {
            "package": "app.vds.player",
            "version_code": 7,
            "version_name": "1.0.7",
            "sha256": SHA,
            "size": len(APK),
        },
        "server_url": "https://vds.example/espace/affichage",
        "pairing_ticket": TICKET,
    }
    return {**base, **kw}


class FakeProvider:
    platforms = frozenset({"android"})

    def __init__(self, slow: float = 0) -> None:
        self.slow = slow
        self.paths: list[Path] = []

    async def provision(
        self, req: ProvisionRequest, apk: ApkSource, report: Any
    ) -> ProvisionOutcome:
        await report(StepReport(step="connect", status="ok"))
        await report(StepReport(step="download", status="running"))
        self.paths.append(await apk.fetch(req.apk))
        await report(StepReport(step="download", status="ok"))
        if self.slow:
            await asyncio.sleep(self.slow)
        return ProvisionOutcome(ok=True, kiosk="soft", installed_version_code=req.apk.version_code)


def runner(
    tmp: Path,
    provider: FakeProvider | None,
    events: list[tuple[str, dict[str, Any]]],
    body: bytes = APK,
    status: int = 200,
    seen: list[httpx.Request] | None = None,
    timeout_s: float = 5,
) -> ProvisionRunner:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, content=body)

    async def emit(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    return ProvisionRunner(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        http_base="https://vds.example/espace/affichage",
        token=TOKEN,
        data_dir=tmp,
        providers=lambda p: provider if p == "android" else None,
        emit=emit,
        local_addresses=lambda: ["192.168.1.2"],
        timeout_s=timeout_s,
    )


async def test_progress_relayed_and_apk_verified(tmp_path: Path) -> None:
    ev: list[tuple[str, dict[str, Any]]] = []
    seen: list[httpx.Request] = []
    prov = FakeProvider()
    status, code, data = await runner(tmp_path, prov, ev, seen=seen).run(SID, args())
    assert (status, code) == ("ok", None) and data["kiosk"] == "soft"
    assert [e[1]["step"] for e in ev] == ["connect", "download", "download"]
    assert all(e[0] == "screen.provision_progressed" and e[1]["screen_id"] == SID for e in ev)
    assert TICKET not in json.dumps(ev)
    assert str(seen[0].url) == "https://vds.example/espace/affichage/api/v1/agent/apk/android/7"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert prov.paths[0] == tmp_path / "apk" / f"{SHA}.apk" and prov.paths[0].read_bytes() == APK
    assert not list((tmp_path / "apk").glob(".*.part"))


async def test_cache_hit_no_second_download(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    r = runner(tmp_path, FakeProvider(), [], seen=seen)
    await r.run(SID, args())
    await r.run(SID, args())
    assert len(seen) == 1


async def test_bad_sha256_is_apk_mismatch(tmp_path: Path) -> None:
    r = runner(tmp_path, FakeProvider(), [], body=b"x" * len(APK))
    status, code, _ = await r.run(SID, args())
    assert (status, code) == ("failed", "provision.android.apk_mismatch")
    assert not list((tmp_path / "apk").glob("*.apk"))


async def test_oversized_stream_rejected(tmp_path: Path) -> None:
    r = runner(tmp_path, FakeProvider(), [], body=APK + b"more")
    assert (await r.run(SID, args()))[1] == "provision.android.apk_mismatch"


async def test_http_error_is_apk_unavailable(tmp_path: Path) -> None:
    r = runner(tmp_path, FakeProvider(), [], status=404)
    assert (await r.run(SID, args()))[1] == "provision.android.apk_unavailable"


@pytest.mark.parametrize("addr", ["8.8.8.8", "127.0.0.1", "192.168.1.2"])
async def test_public_loopback_and_own_address_refused(tmp_path: Path, addr: str) -> None:
    r = runner(tmp_path, FakeProvider(), [])
    a = args()
    a["address"] = addr  # contourne la validation de la trame : l'agent revalide
    # ProvisionRequest refuse déjà publique/loopback ; sa propre IP est refusée par le runner
    if addr == "192.168.1.2":
        assert (await r.run(SID, a))[1] == "provision.address_is_agent"
    else:
        with pytest.raises(ValueError):
            await r.run(SID, a)


async def test_no_provider_and_lock_per_ip_and_timeout(tmp_path: Path) -> None:
    assert (await runner(tmp_path, None, []).run(SID, args()))[1] == "provision.no_provider"
    r = runner(tmp_path, FakeProvider(slow=0.3), [])
    first = asyncio.create_task(r.run(SID, args()))
    await asyncio.sleep(0.1)
    assert (await r.run(str(uuid4()), args()))[1] == "provision.busy"
    assert (await first)[0] == "ok"
    t = runner(tmp_path, FakeProvider(slow=2), [], timeout_s=0.1)
    assert (await t.run(SID, args()))[1] == "provision.timeout"


async def test_module_exception_is_sanitized(tmp_path: Path) -> None:
    class Boom(FakeProvider):
        async def provision(self, *a: Any, **k: Any) -> ProvisionOutcome:
            raise RuntimeError(f"shell: {TICKET}")

    status, code, data = await runner(tmp_path, Boom(), []).run(SID, args())
    assert code == "provision.module_error" and TICKET not in json.dumps(data)


PROV_MODULE = """
from typing import ClassVar
from vds_sdk.agent import PowerDriver, PowerResult, ProvisionOutcome, StepReport

class ProvDriver(PowerDriver):
    capabilities: ClassVar[frozenset] = frozenset({"power.on"})
    platforms: ClassVar[frozenset] = frozenset({"android"})
    async def set_power(self, target, state):
        return PowerResult(ok=True, state="unknown", verified=False)
    async def get_state(self, target):
        return "off"
    async def provision(self, req, apk, report):
        await report(StepReport(step="connect", status="ok"))
        p = await apk.fetch(req.apk)
        await report(StepReport(step="verify", status="ok", detail={"size": p.stat().st_size}))
        return ProvisionOutcome(
            ok=True, kiosk="device_owner", installed_version_code=req.apk.version_code
        )
"""


async def test_agent_routes_screen_provision_to_module(tmp_path: Path) -> None:
    for n in [k for k in sys.modules if k.startswith("vds_mod_power_prov")]:
        del sys.modules[n]
    d = tmp_path / "mods" / "power.prov"
    (d / "agent").mkdir(parents=True)
    (d / "agent" / "module.py").write_text(PROV_MODULE)
    (d / "module.json").write_text(
        json.dumps(
            {
                "id": "power.prov",
                "version": "0.1.0",
                "core": ">=1.3 <2.0",
                "kind": "power-driver",
                "name": {"fr": "P", "en": "P"},
                "description": {"fr": "P", "en": "P"},
                "targets": ["site-agent"],
                "entrypoints": {"site-agent": "agent.module:ProvDriver"},
                "capabilities": ["power.on", "provision.android"],
                "permissions": ["screens.provision"],
                "events": {"listens": [], "emits": []},
                "author": "test",
            }
        )
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=APK)

    async with FakeServer(tmp_path, ["power.prov"]) as srv:
        srv.to_send = [cmd_frame(1, "screen.provision", args(), SID)]
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
        task = asyncio.create_task(agent.run())
        try:
            await eventually(lambda: len(srv.results()) >= 1)
        finally:
            agent.link.stop()
            await asyncio.wait_for(task, 5)
        res = srv.results()[0]
        assert res["status"] == "ok" and res["data"]["installed_version_code"] == 7
        steps = [
            e["payload"]["step"] for e in srv.events() if e["name"] == "screen.provision_progressed"
        ]
        assert steps == ["connect", "verify"]
