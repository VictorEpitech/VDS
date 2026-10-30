"""Exécution de la commande fermée `discovery.scan` (vds-core 1.5).

Le runtime possède le réseau (façade `LanDiscoveryNet`) ; le module `discovery.lan` n'interprète
que les réponses. Verrou « un scan à la fois », fusion par MAC puis IP, périmètre revalidé,
filtrage des pilotes suggérés, plafond de 128 appareils, échéance de 30 s, lots de 16 appareils
au plus une trame toutes les 500 ms. Aucune cible ne vient de la commande.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from vds_sdk.agent import DiscoveredDevice, Discoverer, DiscoveryScope, DiscoveryScopeError
from vds_sdk.agent.discovery import MAX_BATCH, MAX_DEVICES, MAX_SUGGESTED_DRIVERS
from vds_sdk.wire import DiscoveryBatch, DiscoveryResult

from .discovery_net import Iface, LanDiscoveryNet, Perimeter, compute_perimeter, list_interfaces

log = logging.getLogger("vds.agent.discovery")

SCAN_BUDGET_S = 30.0
BATCH_INTERVAL_S = 0.5
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
Result = tuple[str, str | None, dict[str, Any]]
_RANK = {"low": 0, "medium": 1, "high": 2}


def merge_devices(old: DiscoveredDevice, new: DiscoveredDevice) -> DiscoveredDevice:
    drivers = list(dict.fromkeys([*old.suggested_drivers, *new.suggested_drivers]))
    return DiscoveredDevice(
        ip=old.ip,
        mac=old.mac or new.mac,
        vendor=old.vendor or new.vendor,
        model=old.model or new.model,
        name=old.name or new.name,
        platform=old.platform if old.platform != "unknown" else new.platform,
        protocols=old.protocols | new.protocols,
        suggested_drivers=tuple(drivers[:MAX_SUGGESTED_DRIVERS]),
        confidence=max(old.confidence, new.confidence, key=lambda c: _RANK[c]),
    )


class _Collector:
    """Fusion, revalidation du périmètre, plafond, lots."""

    def __init__(
        self,
        scan_id: Any,
        perimeter: Perimeter,
        net: LanDiscoveryNet,
        active_drivers: Callable[[], set[str]],
        emit: Emit,
        interval_s: float,
    ) -> None:
        self.scan_id = scan_id
        self.p = perimeter
        self.net = net
        self.active = active_drivers
        self.emit = emit
        self.interval = interval_s
        self.devices: dict[Any, DiscoveredDevice] = {}  # IPv4Address -> device
        self.dirty: dict[Any, None] = {}
        self.capped = False
        self.seq = 0

    async def report(self, dev: DiscoveredDevice) -> None:
        if not self.p.contains(dev.ip):
            log.warning("discovery.device_out_of_scope")
            return
        if dev.mac is None and (mac := self.net.neighbors().get(dev.ip)):
            dev = dev.model_copy(update={"mac": mac})
        active = self.active()
        dev = dev.model_copy(
            update={"suggested_drivers": tuple(d for d in dev.suggested_drivers if d in active)}
        )
        key = next(
            (ip for ip, d in self.devices.items() if dev.mac and d.mac == dev.mac),
            dev.ip if dev.ip in self.devices else None,
        )
        if key is None:
            if len(self.devices) >= MAX_DEVICES:
                self.capped = True
                return
            self.devices[dev.ip] = dev
            self.dirty[dev.ip] = None
            return
        merged = merge_devices(self.devices[key], dev)
        if merged != self.devices[key]:
            self.devices[key] = merged
            self.dirty[key] = None

    async def flush(self) -> None:
        keys = list(self.dirty)
        self.dirty.clear()
        for i in range(0, len(keys), MAX_BATCH):
            batch = DiscoveryBatch(
                scan_id=self.scan_id,
                seq=self.seq,
                devices=[self.devices[k] for k in keys[i : i + MAX_BATCH]],
            )
            self.seq += 1
            await self.emit("discovery.batch", batch.model_dump(mode="json"))

    async def pump(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            if self.dirty:
                await self.flush()


class DiscoveryRunner:
    def __init__(
        self,
        *,
        provider: Callable[[], Discoverer | None],
        active_drivers: Callable[[], set[str]],
        emit: Emit,
        interfaces: Callable[[], Iterable[Iface]] = list_interfaces,
        net_factory: Callable[[Perimeter, float], LanDiscoveryNet] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        budget_s: float = SCAN_BUDGET_S,
        batch_interval_s: float = BATCH_INTERVAL_S,
    ) -> None:
        self._provider = provider
        self._active = active_drivers
        self._emit = emit
        self._ifaces = interfaces
        self._net_factory = net_factory or (lambda p, dl: LanDiscoveryNet(p, deadline=dl))
        self._clock = clock
        self._budget = budget_s
        self._interval = batch_interval_s
        self._busy = False

    @staticmethod
    def _fail(code: str) -> Result:
        return "failed", code, {}

    async def run(self, args: dict[str, Any]) -> Result:
        if self._busy:
            return self._fail("discovery.busy")
        self._busy = True
        try:
            return await self._run(args)
        finally:
            self._busy = False

    async def _run(self, args: dict[str, Any]) -> Result:
        # Seuls scan_id / scope / probe_ports sont lus : aucune cible réseau n'est acceptée.
        scan_id, probe_ports = args["scan_id"], bool(args["probe_ports"])
        module = self._provider()
        if module is None:
            return self._fail("discovery.no_provider")
        perimeter = compute_perimeter(self._ifaces())
        if perimeter is None:
            return self._fail("discovery.no_private_network")
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        net = self._net_factory(perimeter, loop.time() + self._budget)
        if isinstance(net, LanDiscoveryNet):
            net.probe_ports = probe_ports  # la façade applique le réglage du site (m2)
        scope = DiscoveryScope(
            networks=perimeter.networks,
            truncated=perimeter.truncated,
            probe_ports=probe_ports,
            deadline=self._clock() + timedelta(seconds=self._budget),
        )
        col = _Collector(scan_id, perimeter, net, self._active, self._emit, self._interval)
        pump = asyncio.ensure_future(col.pump())
        try:
            # L'échéance coupe le module : les résultats déjà rapportés sont conservés.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(module.discover(scope, net, col.report), self._budget)
        except DiscoveryScopeError as e:
            log.warning("discovery.scope_error", extra={"code": e.code})
            return self._fail("discovery.internal")
        except Exception as exc:
            # Pas de trace : une ValidationError pydantic embarque IP/MAC/nom (m5).
            log.error("discovery.module_failed", extra={"error": type(exc).__name__})
            return self._fail("discovery.internal")
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            with contextlib.suppress(Exception):
                await net.aclose()
        await col.flush()
        try:
            result = DiscoveryResult(
                scan_id=scan_id,
                networks=list(perimeter.networks),
                truncated=perimeter.truncated,
                device_count=len(col.devices),
                capped=col.capped,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except ValidationError:
            return self._fail("discovery.internal")
        return "ok", None, result.model_dump(mode="json")
