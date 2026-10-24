"""power.samsung-ws : KEY_POWER (bascule) gardée par la lecture d'état, WoL pour l'allumage."""

import asyncio
import json
from typing import Any, ClassVar, Literal

from vds_sdk import ModuleContext
from vds_sdk.agent import (
    Capability,
    DetectHint,
    DetectResult,
    PowerDriver,
    PowerResult,
    PowerState,
    PowerTarget,
)

from .samsung import SamsungError, SamsungTransport, WsSamsungTransport
from .wol import SocketUdpSender, UdpSender, magic_packet

CAPS: frozenset[Capability] = frozenset({"power.on", "power.off", "power.state"})
PAIR_WAIT_S = 45.0
DETECT_INFO_TIMEOUT_S = 5.0
PAIR_DETECT_WAIT_S = 3.0  # sous DETECT_TIMEOUT_S de l'agent (10 s)


class SamsungWsDriver(PowerDriver):
    capabilities: ClassVar[frozenset[Capability]] = CAPS

    def __init__(
        self,
        transport: SamsungTransport | None = None,
        udp: UdpSender | None = None,
        ws_port: int = 8002,
    ) -> None:
        self._ws_port = ws_port
        self._t: SamsungTransport = transport or WsSamsungTransport()
        self._udp: UdpSender = udp or SocketUdpSender()
        self._state: Any = None
        self._pairing: dict[str, asyncio.Task[None]] = {}

    async def setup(self, ctx: ModuleContext) -> None:
        self._state = ctx.state  # type: ignore[attr-defined]

    async def teardown(self) -> None:
        for t in self._pairing.values():
            t.cancel()

    @staticmethod
    def _host(t: PowerTarget) -> str:
        return str(t.address) if t.address else str(t.config.get("host") or "")

    # --- lecture d'état (REST, sans effet) ---------------------------------------
    async def get_state(self, target: PowerTarget) -> PowerState:
        host = self._host(target)
        if not host:
            return "unknown"
        info = await self._t.device_info(host, 3.0)
        if info is None:
            return "off" if target.config.get("unreachable_means_off") else "unknown"
        ps = str((info.get("device") or {}).get("PowerState", "")).lower()
        return "on" if ps == "on" else "standby" if ps == "standby" else "unknown"

    # --- commande -----------------------------------------------------------------
    async def _press_power(self, host: str, target: PowerTarget) -> PowerResult:
        cfg = target.config
        token = await self._state.get(f"token:{host}")
        pin = await self._state.get(f"pin:{host}")
        try:
            s = await self._t.send_key(
                host,
                int(cfg.get("port", 8002)),
                str(cfg.get("name", "VDS")),
                token,
                pin,
                "KEY_POWER",
                8.0,
            )
        except SamsungError as e:
            return PowerResult(ok=False, state="unknown", verified=False, code=e.code)
        await self._remember(host, s.token, s.fingerprint)
        return PowerResult(ok=True, state="unknown", verified=False)

    async def _remember(self, host: str, token: str | None, fp: str) -> None:
        if token:
            await self._state.set(f"token:{host}", token)
        if await self._state.get(f"pin:{host}") is None:
            await self._state.set(f"pin:{host}", fp)  # TOFU : épinglé au premier contact

    async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult:
        host = self._host(target)
        if not host:
            return PowerResult(
                ok=False, state="unknown", verified=False, code="power.samsung.no_host"
            )
        current = await self.get_state(target)
        if current == "on" and state == "on" or current in ("off", "standby") and state == "off":
            return PowerResult(
                ok=True, state=current, verified=True
            )  # KEY_POWER bascule : jamais à l'aveugle
        if state == "off":
            if current != "on":  # injoignable : on ne bascule pas à l'aveugle
                return PowerResult(
                    ok=False, state=current, verified=False, code="power.samsung.unreachable"
                )
            return await self._press_power(host, target)
        # allumage : WoL d'abord (TV en veille réseau), puis touche si l'API répond encore
        mac = target.mac or str(target.config.get("mac") or "")
        sent = False
        if mac:
            try:
                await self._udp.send(magic_packet(mac), "255.255.255.255", 9)
                sent = True
            except (OSError, ValueError):
                pass
        if current == "standby":
            res = await self._press_power(host, target)
            if res.ok or not sent:
                return res
        if sent:
            return PowerResult(ok=True, state="unknown", verified=False)
        return PowerResult(ok=False, state="unknown", verified=False, code="power.samsung.no_mac")

    # --- détection / appairage -------------------------------------------------------
    async def _pair(self, host: str, target_cfg: dict[str, Any]) -> None:
        try:
            s = await self._t.send_key(
                host,
                int(target_cfg.get("port", 8002)),
                str(target_cfg.get("name", "VDS")),
                None,
                await self._state.get(f"pin:{host}"),
                None,
                PAIR_WAIT_S,
            )
            await self._remember(host, s.token, s.fingerprint)
        except SamsungError:
            pass
        finally:
            self._pairing.pop(host, None)

    async def detect(self, hint: DetectHint) -> DetectResult | None:
        if hint.address is None:
            return None
        host = str(hint.address)
        info = await self._t.device_info(host, DETECT_INFO_TIMEOUT_S)  # la TV répond parfois > 3 s
        if info is None or "samsung" not in json.dumps(info).lower():
            return None
        dev = info.get("device") or {}
        mac = hint.mac or str(dev.get("wifiMac") or "").lower() or None
        proposed: dict[str, Any] = {"host": host}
        if mac:
            proposed["mac"] = mac
        action = None
        if await self._state.get(f"token:{host}") is None:
            action = "power.samsung.accept_on_tv"
            if host not in self._pairing:  # affiche la demande sur la TV et attend l'acceptation
                self._pairing[host] = asyncio.create_task(self._pair(host, {"port": self._ws_port}))
            await asyncio.wait({self._pairing[host]}, timeout=PAIR_DETECT_WAIT_S)
            if await self._state.get(f"token:{host}") is not None:
                action = None
        return DetectResult(
            driver="power.samsung-ws",
            confidence=0.9 if str(dev.get("modelName", "")) else 0.7,
            capabilities=CAPS,
            proposed_config=proposed,
            requires_user_action=action,
        )
