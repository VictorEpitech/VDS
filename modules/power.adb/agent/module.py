"""power.adb : KEYCODE_WAKEUP / KEYCODE_SLEEP, état via `dumpsys power`."""

import re
from typing import Any, ClassVar, Literal

from vds_sdk import Health, ModuleContext
from vds_sdk.agent import (
    Capability,
    DetectHint,
    DetectResult,
    PowerDriver,
    PowerResult,
    PowerState,
    PowerTarget,
)

from .adb import AdbError, AdbTransport, TcpAdbTransport, generate_key_pem

KEY_STATE = "adb_private_key"
_WAKE_RE = re.compile(r"mWakefulness=(\w+)")
_DISPLAY_RE = re.compile(r"Display Power: state=(\w+)")
KEYS = {"on": "KEYCODE_WAKEUP", "off": "KEYCODE_SLEEP"}


def parse_state(dumpsys: str) -> PowerState:
    m = _WAKE_RE.search(dumpsys)
    if m:
        return "on" if m.group(1) == "Awake" else "standby"
    m = _DISPLAY_RE.search(dumpsys)
    if m:
        return "on" if m.group(1) == "ON" else "standby"
    return "unknown"


class AdbDriver(PowerDriver):
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {"power.on", "power.off", "power.state"}
    )

    def __init__(self, transport: AdbTransport | None = None) -> None:
        self._transport = transport
        self._ctx: ModuleContext | None = None
        self._last_error: str | None = None

    async def setup(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        if self._transport is None:
            state: Any = ctx.state  # type: ignore[attr-defined]
            pem = await state.get(KEY_STATE)
            if not pem:  # paire de clés propre à l'agent, jamais remontée au serveur
                pem = generate_key_pem()
                await state.set(KEY_STATE, pem)
            self._transport = TcpAdbTransport(pem)

    async def health(self) -> Health:
        if self._last_error:
            return Health(status="degraded", code=self._last_error)
        return Health()

    def _endpoint(self, t: PowerTarget) -> tuple[str, int, float] | None:
        host = str(t.address) if t.address else str(t.config.get("host") or "")
        if not host or self._transport is None:
            return None
        return host, int(t.config.get("port", 5555)), int(t.config.get("timeout_ms", 5000)) / 1000

    async def _shell(self, t: PowerTarget, cmd: str) -> str:
        ep = self._endpoint(t)
        if ep is None:
            raise AdbError("power.adb.no_host")
        assert self._transport is not None  # noqa: S101
        try:
            out = await self._transport.shell(ep[0], ep[1], cmd, ep[2])
        except AdbError as e:
            self._last_error = e.code
            raise
        self._last_error = None
        return out

    async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult:
        try:
            await self._shell(target, f"input keyevent {KEYS[state]}")
        except AdbError as e:
            return PowerResult(ok=False, state="unknown", verified=False, code=e.code)
        return PowerResult(ok=True, state="unknown", verified=False)

    async def get_state(self, target: PowerTarget) -> PowerState:
        try:
            return parse_state(await self._shell(target, "dumpsys power"))
        except AdbError as e:
            if e.code in ("power.adb.unreachable", "power.adb.timeout") and target.config.get(
                "unreachable_means_off"
            ):
                return "off"
            return "unknown"

    async def detect(self, hint: DetectHint) -> DetectResult | None:
        if hint.address is None or self._transport is None:
            return None
        proposed = {"host": str(hint.address), "port": 5555}
        try:
            out = await self._transport.shell(
                str(hint.address), 5555, "getprop ro.product.manufacturer", 5.0
            )
        except AdbError as e:
            if e.code == "power.adb.unauthorized":
                return DetectResult(
                    driver="power.adb",
                    confidence=0.6,
                    capabilities=frozenset({"power.on", "power.off", "power.state"}),
                    proposed_config=proposed,
                    requires_user_action="power.adb.accept_on_tv",
                    code=e.code,
                )
            return None
        boost = 0.1 if hint.platform == "android-tv" else 0.0
        maker = out.strip().lower()
        return DetectResult(
            driver="power.adb",
            confidence=min(1.0, (0.9 if maker == "tcl" else 0.8) + boost),
            capabilities=self.capabilities,
            proposed_config=proposed,
        )
