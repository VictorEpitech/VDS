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
from .cache import MediaCache, SyncResult, fetch_manifest
from .cascade import Cascade, ScreenDrivers, event_for
from .lan import LanServer, parse_listen
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
        self.cache = MediaCache(settings.data_dir / "media", int(settings.cache_max_gb * 1024**3))
        self.site_id: str | None = None
        self.cache_stats = SyncResult(0, 0, 0)
        self._cache_key: tuple[str, bytes] | None = None
        self._cache_lock = asyncio.Lock()
        self.lan: LanServer | None = None

    # --- démarrage --------------------------------------------------------------
    async def start(self) -> None:
        self.discovered = discover(self.s.modules_dir)
        self.plan = await self.store.kv_get("plan", {})
        self.plan_etag = await self.store.kv_get("plan_etag")
        enabled = await self.store.kv_get("modules_enabled")
        if enabled is not None:  # hors ligne : dernier état connu
            await self._activate(enabled)
        await self._load_cache_key()
        self.site_id = await self.store.kv_get("site_id")
        await self._start_lan()

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
        if self.lan:
            await self.lan.stop()
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

    async def _deactivate_absent(self, enabled: list[dict[str, Any]]) -> None:
        """Pilotes chargés mais plus listés (désactivés côté serveur) : teardown et retrait."""
        want = {e["id"] for e in enabled}
        for mid in [m for m in self.drivers if m not in want]:
            ld = self.drivers.pop(mid)
            with contextlib.suppress(Exception):
                await ld.driver.teardown()
            self.observed = {k: v for k, v in self.observed.items() if v.get("driver") != mid}

    # --- handshake ----------------------------------------------------------------
    async def _hello(self, last_server_seq: int) -> AgentHello:
        return AgentHello(
            agent_version=AGENT_VERSION,
            core=CORE_API_VERSION,
            modules=[
                ModuleRef(id=d.manifest.id, version=d.manifest.version) for d in self.discovered
            ],
            last_server_seq=last_server_seq,
            out_seq=int(await self.store.kv_get("out_seq", 0)),
            host=HostInfo(os=platform.system().lower(), arch=platform.machine()),
            lan=LanInfo(ipv4=lan_ipv4()),
        )

    async def _welcomed(self, w: AgentWelcome) -> None:
        self._clock_offset_ms = int((w.server_time - self.clock()).total_seconds() * 1000)
        enabled = [e.model_dump() for e in w.modules_enabled]
        await self.store.kv_set("modules_enabled", enabled)
        await self._activate(enabled)
        await self._deactivate_absent(enabled)
        self.site_id = str(w.site_id)
        await self.store.kv_set("site_id", self.site_id)
        await self._store_cache_key(w.cache.key_id, w.cache.key)
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
            "cache": {
                "enabled": self.lan is not None,
                "bytes": self.cache_stats.bytes,
                "items": self.cache_stats.items,
                "missing": self.cache_stats.missing,
            },
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
                log.warning(
                    "power.driver_skipped",
                    extra={"driver": drv_id, "loaded": ld is not None, "configured": drv_id in configs},
                )
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
            sd = self.build_screen(entry)
            log.info(
                "power.cascade_start",
                extra={
                    "screen_id": screen_id,
                    "wanted": state,
                    "plan_cascade": entry.get("cascade", []),
                    "active": [d[0] for d in sd.drivers],
                    "loaded": sorted(self.drivers),
                },
            )
            outcome = await self._cascade_for(entry).run(sd, state)
            log.info(
                "power.cascade_end",
                extra={"screen_id": screen_id, "ok": outcome.ok, "code": outcome.code,
                       "driver": outcome.driver, "attempted": outcome.attempted},
            )
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
        t0 = time.monotonic()
        log.info("agent.cmd_start", extra={"cmd": cmd.name, "screen_id": cmd.screen_id})
        try:
            status, code, data = await self.dispatch(cmd, args.model_dump())
            log.info(
                "agent.cmd_end",
                extra={
                    "cmd": cmd.name,
                    "status": status,
                    "code": code,
                    "ms": int((time.monotonic() - t0) * 1000),
                },
            )
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
        if cmd.name == "cache.sync":
            return await self.sync_cache()
        return "failed", "agent.unknown_command", {}

    # --- cache média ------------------------------------------------------------------
    async def _load_cache_key(self) -> None:
        stored = await self.store.state_get("agent.cache", "lan_key")  # chiffré (Fernet)
        if stored:
            self._cache_key = (stored["kid"], bytes.fromhex(stored["key"]))

    async def _store_cache_key(self, kid: str | None, key: str | None) -> None:
        if kid and key:
            self._cache_key = (kid, bytes.fromhex(key))
            await self.store.state_set("agent.cache", "lan_key", {"kid": kid, "key": key})
        else:  # le serveur ne fournit plus de clé : le service LAN refuse tout
            self._cache_key = None
            await self.store.state_delete("agent.cache", "lan_key")

    async def _start_lan(self) -> None:
        if self.s.cache_lan_listen is None or self.lan is not None:
            return  # désactivé par défaut : aucun port en écoute
        try:
            parse_listen(self.s.cache_lan_listen)
        except ValueError:
            log.warning("agent.cache_lan_listen_invalid")
            return
        self.lan = LanServer(self.cache, self.s.cache_lan_listen, lambda: self._cache_key)
        await self.lan.start()

    async def sync_cache(self) -> tuple[str, str | None, dict[str, Any]]:
        async with self._cache_lock:
            try:
                manifest = await fetch_manifest(self._http, self.s.http_base, self.s.token)
            except (httpx.HTTPError, ValueError):
                log.warning("agent.cache_manifest_failed")
                return "failed", "agent.cache_unavailable", {}
            res = await self.cache.sync(self._http, self.s.http_base, self.s.token, manifest)
        self.cache_stats = res
        data = {"items": res.items, "bytes": res.bytes, "missing": res.missing}
        await self.link.emit("cache.synced", {"site_id": self.site_id, **data})
        if res.missing:
            return "failed", "agent.cache_incomplete", data
        return "ok", None, data

    async def probe(self, screen_id: str) -> tuple[str | None, PowerState]:
        entry = self.screen_entry(screen_id)
        if entry is None:
            return None, "unknown"
        sd = self.build_screen(entry)
        log.info("power.probe", extra={"screen_id": screen_id, "drivers": [d[0] for d in sd.drivers]})
        for drv_id, drv, tgt in sd.drivers:
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
            t0 = time.monotonic()
            driver = ld.manifest.id
            try:
                res = await asyncio.wait_for(ld.driver.detect(hint), DETECT_TIMEOUT_S)
            except TimeoutError:
                log.warning("power.detect_timeout", extra={"driver": driver, "address": address})
                return None
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "power.detect_error",
                    extra={"driver": driver, "err": type(e).__name__, "detail": str(e)},
                )
                return None
            log.info(
                "power.detect_result",
                extra={
                    "driver": driver,
                    "address": address,
                    "found": res is not None,
                    "action": res.requires_user_action if res else None,
                    "ms": int((time.monotonic() - t0) * 1000),
                },
            )
            return res

        found = await asyncio.gather(*(one(d) for d in self.drivers.values()))
        return sorted((r for r in found if r), key=lambda r: -r.confidence)
