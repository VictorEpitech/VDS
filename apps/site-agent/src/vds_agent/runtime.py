"""Runtime de l'agent : modules, plan, commandes, planificateur autonome, heartbeat."""

import asyncio
import contextlib
import ipaddress
import logging
import platform
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import ValidationError
from vds_sdk import CORE_API_VERSION
from vds_sdk.agent import DetectHint, DetectResult, PowerState, PowerTarget
from vds_sdk.wire import (
    AgentHello,
    AgentWelcome,
    Cmd,
    Frame,
    HostInfo,
    LanInfo,
    ModuleRef,
)

from .bridge import AgentBus
from .cascade import Cascade, ScreenDrivers, event_for
from .link import ServerLink
from .modules import Discovered, LoadedDriver, apply_defaults, discover, instantiate
from .plan import current_slot, fetch_plan
from .settings import AGENT_VERSION, AgentSettings
from .store import ModuleState, Store

log = logging.getLogger("vds.agent")

DETECT_TIMEOUT_S = 10.0
SCHEDULE_TICK_S = 15.0
RETRY_AFTER_FAIL_S = 300.0

Clock = Callable[[], datetime]


def lan_ipv4() -> list[str]:
    """IP LAN locale (aucun paquet émis : connect UDP seulement)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return [str(s.getsockname()[0])]
    except OSError:
        return []


def arp_lookup(address: str, table: Path = Path("/proc/net/arp")) -> str | None:
    try:
        for line in table.read_text().splitlines()[1:]:
            cols = line.split()
            if len(cols) >= 4 and cols[0] == address and cols[3] != "00:00:00:00:00:00":
                return cols[3]
    except OSError:
        return None
    return None


class Agent:
    def __init__(
        self,
        settings: AgentSettings,
        store: Store,
        *,
        cascade: Cascade | None = None,
        clock: Clock = lambda: datetime.now(UTC),
        http: httpx.AsyncClient | None = None,
        heartbeat_s: float = 60.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.s = settings
        self.store = store
        self.cascade = cascade or Cascade()
        self.clock = clock
        self._http = http or httpx.AsyncClient(
            verify=settings.ssl_context(), timeout=15, follow_redirects=False
        )
        self.link = ServerLink(
            settings,
            store,
            build_hello=self._hello,
            build_heartbeat=self._heartbeat,
            on_welcome=self._welcomed,
            on_cmd=self._on_cmd,
            sleep=sleep,
            heartbeat_s=heartbeat_s,
        )
        self.discovered: list[Discovered] = []
        self.drivers: dict[str, LoadedDriver] = {}
        self.plan: dict[str, Any] = {}
        self.plan_etag: str | None = None
        self.observed: dict[str, dict[str, Any]] = {}
        self._started = time.monotonic()
        self._clock_offset_ms = 0
        self._tasks: set[asyncio.Task[Any]] = set()
        self._retry_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # --- démarrage --------------------------------------------------------------
    async def start(self) -> None:
        self.discovered = discover(self.s.modules_dir)
        self.plan = await self.store.kv_get("plan", {})
        self.plan_etag = await self.store.kv_get("plan_etag")
        enabled = await self.store.kv_get("modules_enabled")
        if enabled is not None:  # hors ligne : dernier état connu
            await self._activate(enabled)

    async def run(self) -> None:
        await self.start()
        sched = asyncio.create_task(self._scheduler())
        try:
            await self.link.run()
        finally:
            sched.cancel()
            for t in list(self._tasks):
                t.cancel()
            await self.close()

    async def close(self) -> None:
        for d in self.drivers.values():
            with contextlib.suppress(Exception):
                await d.driver.teardown()
        await self._http.aclose()

    async def _activate(self, enabled: list[dict[str, Any]]) -> None:
        want = {e["id"]: e["version"] for e in enabled}
        for d in self.discovered:
            m = d.manifest
            if m.kind != "power-driver" or d.error or want.get(m.id) != m.version:
                continue
            if m.id in self.drivers:
                continue
            try:
                self.drivers[m.id] = await instantiate(
                    d, AgentBus(self.link), ModuleState(self.store, m.id)
                )
            except Exception:
                log.exception("module.setup_failed", extra={"module": m.id})

    # --- handshake ----------------------------------------------------------------
    async def _hello(self, last_server_seq: int) -> AgentHello:
        return AgentHello(
            agent_version=AGENT_VERSION,
            core=CORE_API_VERSION,
            modules=[
                ModuleRef(id=d.manifest.id, version=d.manifest.version) for d in self.discovered
            ],
            last_server_seq=last_server_seq,
            host=HostInfo(os=platform.system().lower(), arch=platform.machine()),
            lan=LanInfo(ipv4=lan_ipv4()),
        )

    async def _welcomed(self, w: AgentWelcome) -> None:
        self._clock_offset_ms = int((w.server_time - self.clock()).total_seconds() * 1000)
        enabled = [e.model_dump() for e in w.modules_enabled]
        await self.store.kv_set("modules_enabled", enabled)
        await self._activate(enabled)
        if w.plan_revision and w.plan_revision != self.plan.get("revision"):
            self._spawn(self.refresh_plan())

    async def _heartbeat(self) -> dict[str, Any]:
        mods: list[dict[str, Any]] = []
        for d in self.discovered:
            ld = self.drivers.get(d.manifest.id)
            if d.error:
                mods.append({"id": d.manifest.id, "status": "down", "code": d.error})
            elif ld is None:
                mods.append({"id": d.manifest.id, "status": "down", "code": "module.not_enabled"})
            else:
                h = await ld.driver.health()
                mods.append({"id": d.manifest.id, "status": h.status, "code": h.code})
        return {
            "uptime_s": int(time.monotonic() - self._started),
            "clock_offset_ms": self._clock_offset_ms,
            "plan_revision": self.plan.get("revision"),
            "modules": mods,
            "screens": [{"screen_id": k, "power": v} for k, v in self.observed.items()],
        }

    # --- plan ---------------------------------------------------------------------
    async def refresh_plan(self) -> bool:
        try:
            res = await fetch_plan(self._http, self.s.http_base, self.s.token, self.plan_etag)
        except httpx.HTTPError:
            log.warning("agent.plan_fetch_failed")
            return False
        if res is None:
            return True
        self.plan, self.plan_etag = res
        await self.store.kv_set("plan", self.plan)
        await self.store.kv_set("plan_etag", self.plan_etag)
        return True

    def screen_entry(self, screen_id: str) -> dict[str, Any] | None:
        for s in self.plan.get("screens", []):
            if s.get("screen_id") == screen_id:
                return dict(s)
        return None

    def build_screen(self, entry: dict[str, Any]) -> ScreenDrivers:
        sid = UUID(entry["screen_id"])
        tz = entry.get("timezone", "Europe/Paris")
        sd = ScreenDrivers(sid, tz)
        configs: dict[str, Any] = entry.get("drivers", {})
        for drv_id in entry.get("cascade", []):
            ld = self.drivers.get(drv_id)
            if ld is None or drv_id not in configs:
                continue  # inactif ou non configuré pour cet écran
            try:
                cfg = apply_defaults(ld.ctx.schema, configs[drv_id])
            except ValueError:
                log.warning("power.config_invalid", extra={"driver": drv_id})
                continue
            ld.ctx.screen_configs[str(sid)] = cfg
            host = cfg.get("host")
            addr = None
            if host:
                with contextlib.suppress(ValueError):
                    addr = ipaddress.ip_address(host)
            sd.drivers.append(
                (
                    drv_id,
                    ld.driver,
                    PowerTarget(
                        screen_id=sid,
                        site_timezone=tz,
                        address=addr,
                        mac=cfg.get("mac"),
                        config=cfg,
                    ),
                )
            )
        return sd

    def _cascade_for(self, entry: dict[str, Any]) -> Cascade:
        return Cascade(
            retries=int(entry.get("retries", self.cascade.retries)),
            verify_delay_s=float(entry.get("verify_delay_s", self.cascade.verify_delay_s)),
            call_timeout_s=self.cascade.call_timeout_s,
            read_timeout_s=self.cascade.read_timeout_s,
            sleep=self.cascade._sleep,  # noqa: SLF001
        )

    async def apply_power(
        self,
        screen_id: str,
        state: Literal["on", "off"],
        request_id: str | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        entry = self.screen_entry(screen_id)
        if entry is None:
            return False, "power.unknown_screen", {}
        lock = self._locks.setdefault(screen_id, asyncio.Lock())
        async with lock:
            outcome = await self._cascade_for(entry).run(self.build_screen(entry), state)
        name, payload = event_for(outcome, UUID(screen_id), request_id)
        await self.link.emit(name, payload)
        self.observed[screen_id] = {
            "observed": state if outcome.ok and outcome.verified else "unknown",
            "driver": outcome.driver,
            "at": self.clock().isoformat(),
        }
        return outcome.ok, outcome.code if not outcome.ok else None, payload

    # --- planificateur autonome (fonctionne sans serveur) ---------------------------
    async def _scheduler(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("agent.tick_failed")
            await asyncio.sleep(SCHEDULE_TICK_S)

    async def tick(self) -> None:
        now = self.clock()
        mono = time.monotonic()
        for entry in self.plan.get("screens", []):
            sid = entry["screen_id"]
            slot = current_slot(entry.get("power", []), now)
            if slot is None:
                continue
            key, state = slot
            applied = await self.store.kv_get(f"applied:{sid}")
            if applied == key or mono < self._retry_at.get(sid, 0):
                continue
            ok, _, _ = await self.apply_power(sid, state)
            if ok:
                await self.store.kv_set(f"applied:{sid}", key)
                self._retry_at.pop(sid, None)
            else:
                self._retry_at[sid] = mono + RETRY_AFTER_FAIL_S

    # --- commandes ------------------------------------------------------------------
    def _spawn(self, coro: Awaitable[Any]) -> None:
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _on_cmd(self, frame: Frame) -> None:
        self._spawn(self._handle_cmd(frame))

    async def _result(
        self, cmd_id: UUID, status: str, code: str | None, data: dict[str, Any]
    ) -> None:
        d: dict[str, Any] = {"cmd_id": str(cmd_id), "status": status, "data": data}
        if code:
            d["code"] = code
        await self.link.send_reliable("cmd.result", d)

    async def _handle_cmd(self, frame: Frame) -> None:
        try:
            cmd = Cmd.model_validate(frame.data)
        except ValidationError:
            return
        if await self.store.cmd_seen(str(cmd.cmd_id)):
            return
        await self.store.cmd_mark(str(cmd.cmd_id), time.time())
        if cmd.expires_at <= self.clock():
            await self._result(cmd.cmd_id, "expired", None, {})
            return
        try:
            args = cmd.validate_for("agent")
        except (ValueError, ValidationError) as e:
            reject = "unknown_command" if str(e) == "unknown_command" else "invalid_args"
            await self._result(cmd.cmd_id, "rejected", reject, {})
            return
        try:
            status, code, data = await self.dispatch(cmd, args.model_dump())
        except Exception:
            log.exception("agent.cmd_failed", extra={"cmd": cmd.name})
            status, code, data = "failed", "agent.internal", {}
        await self._result(cmd.cmd_id, status, code, data)

    async def dispatch(
        self, cmd: Cmd, args: dict[str, Any]
    ) -> tuple[str, str | None, dict[str, Any]]:
        sid = str(cmd.screen_id) if cmd.screen_id else None
        if cmd.name == "power.set":
            if sid is None:
                return "rejected", "invalid_args", {}
            ok, code, payload = await self.apply_power(sid, args["state"], args["request_id"])
            return ("ok" if ok else "failed"), code, payload
        if cmd.name == "power.probe":
            if sid is None:
                return "rejected", "invalid_args", {}
            drv, state = await self.probe(sid)
            return "ok", None, {"state": state, "driver": drv}
        if cmd.name == "power.detect":
            res = await self.detect(args.get("address"), args.get("mac"))
            return "ok", None, {"results": [r.model_dump(mode="json") for r in res]}
        if cmd.name == "plan.updated":
            ok = await self.refresh_plan()
            await self.tick()
            return ("ok" if ok else "failed"), (None if ok else "agent.plan_fetch_failed"), {}
        if cmd.name == "config.updated":
            return "ok", None, {}  # la config pilote vient du plan (rafraîchi par plan.updated)
        return "failed", "agent.cache_unavailable", {}  # cache.sync : lot T9

    async def probe(self, screen_id: str) -> tuple[str | None, PowerState]:
        entry = self.screen_entry(screen_id)
        if entry is None:
            return None, "unknown"
        for drv_id, drv, tgt in self.build_screen(entry).drivers:
            if "power.state" in drv.capabilities:
                st = await self.cascade.read_state(drv, tgt)
                self.observed[screen_id] = {
                    "observed": st,
                    "driver": drv_id,
                    "at": self.clock().isoformat(),
                }
                return drv_id, st
        return None, "unknown"

    async def detect(self, address: str | None, mac: str | None) -> list[DetectResult]:
        if address and not mac:
            mac = arp_lookup(address)
        hint = DetectHint(address=address, mac=mac)  # type: ignore[arg-type]

        async def one(ld: LoadedDriver) -> DetectResult | None:
            try:
                return await asyncio.wait_for(ld.driver.detect(hint), DETECT_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                return None

        found = await asyncio.gather(*(one(d) for d in self.drivers.values()))
        return sorted((r for r in found if r), key=lambda r: -r.confidence)
