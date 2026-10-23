"""power.wol : allumage par paquet magique ; ni extinction ni lecture d'état."""

import asyncio
from typing import ClassVar, Literal

from vds_sdk.agent import (
    Capability,
    DetectHint,
    DetectResult,
    PowerDriver,
    PowerResult,
    PowerState,
    PowerTarget,
)

from .wol import SocketUdpSender, UdpSender, magic_packet, normalize_mac


class WolDriver(PowerDriver):
    capabilities: ClassVar[frozenset[Capability]] = frozenset({"power.on"})

    def __init__(self, sender: UdpSender | None = None) -> None:
        self._sender = sender or SocketUdpSender()

    async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult:
        if state != "on":
            return PowerResult(
                ok=False, state="unknown", verified=False, code="power.wol.unsupported"
            )
        mac = target.mac or str(target.config.get("mac") or "")
        try:
            packet = magic_packet(mac)
        except ValueError:
            return PowerResult(
                ok=False, state="unknown", verified=False, code="power.wol.mac_invalid"
            )
        broadcast = str(target.config.get("broadcast", "255.255.255.255"))
        port = int(target.config.get("port", 9))
        try:
            for i in range(2):  # un seul paquet se perd facilement sur Wi-Fi/VLAN chargé
                if i:
                    await asyncio.sleep(0.2)
                await self._sender.send(packet, broadcast, port)
        except OSError:
            return PowerResult(
                ok=False, state="unknown", verified=False, code="power.wol.send_failed"
            )
        return PowerResult(ok=True, state="unknown", verified=False)

    async def get_state(self, target: PowerTarget) -> PowerState:
        return "unknown"

    async def detect(self, hint: DetectHint) -> DetectResult | None:
        if not hint.mac:
            return None
        try:
            normalize_mac(hint.mac)
        except ValueError:
            return None
        return DetectResult(
            driver="power.wol",
            confidence=0.3,
            capabilities=self.capabilities,
            proposed_config={"mac": hint.mac.lower().replace("-", ":")},
        )
