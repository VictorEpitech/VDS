"""discovery.lan : interprète les réponses réseau en « appareils présumés ».

Ce module ne possède aucun socket : tout passe par la façade `DiscoveryNet` du runtime, qui
borne les adresses, les ports, les débits et les tailles. Aucun appareil n'est contacté autrement
(pas d'ADB, de WebSocket Samsung ni de HTTP), aucun identifiant n'est utilisé.
"""

import asyncio
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address
from typing import ClassVar

from vds_sdk import Module
from vds_sdk.agent import (
    DISCOVERY_PORTS,
    DiscoveredDevice,
    DiscoveryNet,
    DiscoveryScope,
    SsdpDescription,
    SsdpReply,
)

from .fingerprints import MDNS_SERVICES, SSDP_TARGETS, Evidence, to_device

MDNS_WINDOW_S = 4.0
SSDP_WINDOW_S = 4.0

Report = Callable[[DiscoveredDevice], Awaitable[None]]


class LanDiscoverer(Module):
    scopes: ClassVar[frozenset[str]] = frozenset({"lan"})

    async def discover(self, scope: DiscoveryScope, net: DiscoveryNet, report: Report) -> None:
        ev: dict[IPv4Address, Evidence] = {}

        def of(ip: IPv4Address) -> Evidence:
            return ev.setdefault(ip, Evidence())

        async def mdns() -> None:
            async for rec in net.mdns_browse(MDNS_SERVICES, MDNS_WINDOW_S):
                of(rec.ip).mdns.append(rec)

        async def ssdp() -> None:
            replies: list[SsdpReply] = []
            async for r in net.ssdp_search(SSDP_TARGETS, SSDP_WINDOW_S):
                of(r.ip).ssdp.append(r)
                replies.append(r)
            descs = await asyncio.gather(*(self._describe(net, r) for r in replies))
            for r, d in zip(replies, descs, strict=True):
                if d is not None:
                    of(r.ip).descriptions.append(d)

        async def ports() -> None:
            async for ip, open_ports in net.sweep(DISCOVERY_PORTS):
                of(ip).ports |= open_ports

        jobs = [mdns(), ssdp()]
        if scope.probe_ports:
            jobs.append(ports())
        # Une source en échec ne doit pas faire perdre les autres.
        await asyncio.gather(*jobs, return_exceptions=True)

        neighbors = net.neighbors()
        for ip, e in ev.items():
            device = to_device(ip, e, neighbors.get(ip))
            if device is not None:  # seuls les hôtes avec signal TV sortent de l'agent
                await report(device)

    @staticmethod
    async def _describe(net: DiscoveryNet, reply: SsdpReply) -> SsdpDescription | None:
        try:
            return await net.ssdp_description(reply)
        except Exception:  # noqa: BLE001 - une description en échec n'invalide pas la réponse SSDP
            return None
