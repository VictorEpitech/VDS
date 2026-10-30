"""Scans de découverte LAN : capacités, quotas, expiration, réception des lots et du résultat.

Le noyau n'envoie aucune cible à l'agent (anti-SSRF) : la commande `discovery.scan` ne porte que
l'id du scan et `probe_ports`. Le serveur revalide tout ce que l'agent remonte (RFC1918, /22 au
plus, 128 appareils au plus) avant de le stocker.
"""

import ipaddress
import logging
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.agent.discovery import MAX_BATCH, MAX_DEVICES, DiscoveredDevice
from vds_sdk.versions import Version
from vds_sdk.wire import DiscoveryResult, DiscoveryScanArgs

from vds_server.agents.queue import CommandQueue
from vds_server.agents.service import agent_status
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen, Site
from vds_server.db.models_discovery import ACTIVE, DiscoveryDevice, DiscoveryScan
from vds_server.db.models_power import SiteAgent
from vds_server.discovery import schemas as sc

log = logging.getLogger("vds.discovery")

CMD_NAME = "discovery.scan"
MIN_CORE = Version(1, 5, 0)
SCAN_TTL_S = 60  # expires_at de la commande ; sans cmd.result à 60 s : discovery.timeout
COOLDOWN_S = 60  # entre deux scans d'un site
QUOTA_PER_HOUR = 20
RETENTION = timedelta(hours=24)
BATCH_EVENT = "discovery.batch"  # trame `event` agent -> serveur portant un DiscoveryBatch
RFC1918 = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_CODE_RE = re.compile(r"[a-z0-9_.]{1,96}")

# Namespace -> {clé: "ip"|"mac"} des pilotes actifs (annotation `x-vds-device-address`).
AddressFields = Callable[[], dict[str, dict[str, str]]]
# (id, version) des modules site-agent actifs déclarant `discovery.lan`.
DiscoveryModules = Callable[[], list[tuple[str, str]]]


def _now() -> datetime:
    return datetime.now(UTC)


def _private(ip: ipaddress.IPv4Address) -> bool:
    return any(ip in n for n in RFC1918)


def _valid_networks(nets: list[ipaddress.IPv4Network]) -> bool:
    return all(n.prefixlen >= 22 and any(n.subnet_of(r) for r in RFC1918) for n in nets)


def _host(value: object) -> str:
    return str(value).split("/")[0]


def _clean_code(code: str | None, default: str) -> str:
    return code if isinstance(code, str) and _CODE_RE.fullmatch(code) else default


async def _publish(bus: EventBus | None, events: list[Event]) -> None:
    if bus is not None:
        for event in events:
            await bus.publish(event)


async def finish(
    session: AsyncSession,
    row: DiscoveryScan,
    status: str,
    code: str | None = None,
    *,
    duration_ms: int | None = None,
) -> list[Event]:
    """Clôt le scan : statut, audit `site.discovery.finished`, événement completed|failed."""
    row.status = status
    row.error_code = code
    row.finished_at = _now()
    count = (
        await session.execute(
            select(func.count())
            .select_from(DiscoveryDevice)
            .where(DiscoveryDevice.scan_id == row.id)
        )
    ).scalar_one()
    audit.record(
        session,
        actor_kind="system",
        actor_id=None,
        action="site.discovery.finished",
        target_type="site",
        target_id=str(row.site_id),
        details={
            "scan_id": str(row.id),
            "status": status,
            "device_count": count,
            "networks": [str(n) for n in row.networks or []],
        },
    )
    base = {"scan_id": str(row.id), "site_id": str(row.site_id)}
    if status == "completed":
        if duration_ms is None:
            duration_ms = int((row.finished_at - row.created_at).total_seconds() * 1000)
        payload = {
            **base,
            "device_count": count,
            "capped": row.capped,
            "truncated": row.truncated,
            "duration_ms": duration_ms,
        }
        return [Event(name="discovery.completed", payload=payload, source="core")]
    return [Event(name="discovery.failed", payload={**base, "code": code}, source="core")]


async def expire_stale(session: AsyncSession, row: DiscoveryScan) -> list[Event]:
    """Scan actif sans résultat après 60 s : `failed/discovery.timeout` (expiration paresseuse)."""
    if row.status not in ACTIVE or _now() - row.created_at <= timedelta(seconds=SCAN_TTL_S):
        return []
    if row.cmd_id is not None:
        await session.execute(
            text(
                "UPDATE remote_command SET status = 'expired', code = 'discovery.timeout',"
                " done_at = now() WHERE id = :id AND status IN ('queued','sent')"
            ),
            {"id": row.cmd_id},
        )
    return await finish(session, row, "failed", "discovery.timeout")


def to_out(
    row: DiscoveryScan, devices: list[DiscoveryDevice], known: dict[UUID, UUID]
) -> sc.ScanOut:
    return sc.ScanOut(
        id=row.id,
        status=row.status,  # type: ignore[arg-type]
        error_code=row.error_code,
        created_at=row.created_at,
        finished_at=row.finished_at,
        networks=[str(n) for n in row.networks or []],
        truncated=row.truncated,
        capped=row.capped,
        devices=[
            sc.DeviceOut(
                id=d.id,
                ip=_host(d.ip),
                mac=str(d.mac) if d.mac else None,
                vendor=d.vendor,
                model=d.model,
                name=d.name,
                platform=d.platform,
                protocols=sorted(d.protocols or []),
                suggested_drivers=list(d.suggested_drivers or []),
                confidence=d.confidence,
                known_screen_id=known.get(d.id),
            )
            for d in devices
        ],
    )


def _mac_key(value: object) -> str:
    return re.sub(r"[^0-9a-f]", "", str(value).lower())


class DiscoveryService:
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        queue: CommandQueue,
        modules: DiscoveryModules,
        schemas: ConfigSchemaProvider,
        address_fields: AddressFields,
    ) -> None:
        self._s, self._bus, self._actor, self._queue = session, bus, actor, queue
        self._modules, self._schemas, self._fields = modules, schemas, address_fields

    # --- capacités ---------------------------------------------------------------
    async def _site(self, site_id: UUID) -> Site:
        site = await self._s.get(Site, site_id)
        if site is None:
            raise ApiError("site.not_found", 404)
        return site

    async def _agent(self, site_id: UUID) -> SiteAgent | None:
        stmt = select(SiteAgent).where(SiteAgent.site_id == site_id, SiteAgent.revoked_at.is_(None))
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def _probe_ports(self, site_id: UUID, module_id: str) -> bool:
        try:
            res = await ConfigService(self._s, self._schemas).resolve("site", site_id, module_id)
        except Exception:  # noqa: BLE001 - config illisible : valeur par défaut du module (activé)
            return True
        key = res.values.get("probe_ports")
        return key.value is not False if key is not None else True

    async def capabilities(self, site_id: UUID) -> sc.CapabilitiesOut:
        await self._site(site_id)

        def no(reason: sc.Reason, probe: bool = True) -> sc.CapabilitiesOut:
            return sc.CapabilitiesOut(available=False, reason=reason, probe_ports=probe)

        agent = await self._agent(site_id)
        if agent is None:  # aucun agent actif : même réponse partout, sans lire le mode
            return no("agent_missing")
        if agent_status(agent) != "online":
            return no("agent_offline")
        hello = agent.last_hello if isinstance(agent.last_hello, dict) else {}
        try:
            if Version.parse(str(hello.get("core", "0"))) < MIN_CORE:
                return no("agent_outdated")
        except ValueError:
            return no("agent_outdated")
        modules = self._modules()
        if not modules:
            return no("module_disabled")
        reported = {
            m.get("id"): m.get("version") for m in hello.get("modules", []) if isinstance(m, dict)
        }
        usable: str | None = None
        for mid, version in modules:
            try:
                if mid in reported and Version.parse(str(reported[mid])) >= Version.parse(version):
                    usable = mid
                    break
            except ValueError:
                continue
        if usable is None:
            return no("agent_outdated")
        probe = await self._probe_ports(site_id, usable)
        return sc.CapabilitiesOut(available=True, reason=None, probe_ports=probe)

    # --- création ----------------------------------------------------------------
    async def create(self, site_id: UUID) -> DiscoveryScan:
        site = await self._site(site_id)
        if site.archived_at is not None:
            raise ApiError("site.archived", 409)
        caps = await self.capabilities(site_id)
        agent = await self._agent(site_id)
        if not caps.available or agent is None:
            raise ApiError("discovery.unavailable", 409, reason=caps.reason or "agent_missing")
        now = _now()
        await self._s.execute(
            delete(DiscoveryScan).where(DiscoveryScan.created_at < now - RETENTION)
        )
        events: list[Event] = []
        for stale in (
            await self._s.execute(
                select(DiscoveryScan)
                .where(DiscoveryScan.site_id == site_id, DiscoveryScan.status.in_(ACTIVE))
                .with_for_update()
            )
        ).scalars():
            events += await expire_stale(self._s, stale)
        await self._s.flush()
        active = (
            await self._s.execute(
                select(DiscoveryScan.id).where(
                    DiscoveryScan.site_id == site_id, DiscoveryScan.status.in_(ACTIVE)
                )
            )
        ).first()
        if active is not None:
            await self._s.commit()
            await _publish(self._bus, events)
            raise ApiError("discovery.busy", 409)
        recent = list(
            (
                await self._s.execute(
                    select(DiscoveryScan.created_at)
                    .where(
                        DiscoveryScan.site_id == site_id,
                        DiscoveryScan.created_at > now - timedelta(hours=1),
                    )
                    .order_by(DiscoveryScan.created_at.desc())
                )
            ).scalars()
        )
        wait = 0
        if recent and (left := COOLDOWN_S - (now - recent[0]).total_seconds()) > 0:
            wait, code = math.ceil(left), "discovery.cooldown"
        elif len(recent) >= QUOTA_PER_HOUR:
            oldest = recent[QUOTA_PER_HOUR - 1]
            wait = max(1, math.ceil((oldest + timedelta(hours=1) - now).total_seconds()))
            code = "discovery.quota"
        if wait:
            await self._s.commit()
            await _publish(self._bus, events)
            raise ApiError(code, 429, retry_after_s=wait)
        row = DiscoveryScan(
            site_id=site_id,
            agent_id=agent.id,
            requested_by=self._actor.user_id,
            status="pending",
            probe_ports=caps.probe_ports,
            networks=[],
            truncated=False,
            capped=False,
        )
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("discovery.busy", 409) from exc
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="site.discovery.started",
            target_type="site",
            target_id=str(site_id),
            details={"scan_id": str(row.id), "probe_ports": caps.probe_ports},
        )
        args = DiscoveryScanArgs(scan_id=row.id, probe_ports=caps.probe_ports)
        await self._s.commit()
        try:
            cmd = await self._queue.enqueue(
                "agent",
                agent.id,
                CMD_NAME,
                args=args.model_dump(mode="json"),
                ttl_s=SCAN_TTL_S,
                created_by=self._actor.user_id,
            )
        except Exception:
            events += await finish(self._s, row, "failed", "discovery.enqueue_failed")
            await self._s.commit()
            await _publish(self._bus, events)
            raise
        row.cmd_id = cmd.id
        await self._s.commit()
        events.append(
            Event(
                name="discovery.started",
                payload={
                    "scan_id": str(row.id),
                    "site_id": str(site_id),
                    "agent_id": str(agent.id),
                    "requested_by": self._actor.actor_id,
                    "probe_ports": caps.probe_ports,
                },
                source="core",
            )
        )
        await _publish(self._bus, events)
        return row

    # --- lecture ----------------------------------------------------------------------
    async def _read(self, stmt: Any) -> sc.ScanOut:
        row = (await self._s.execute(stmt.with_for_update())).scalar_one_or_none()
        if row is None:
            raise ApiError("discovery.not_found", 404)
        events = await expire_stale(self._s, row)
        await self._s.commit()
        await _publish(self._bus, events)
        devices = list(
            (
                await self._s.execute(
                    select(DiscoveryDevice)
                    .where(DiscoveryDevice.scan_id == row.id)
                    .order_by(DiscoveryDevice.ip)
                )
            ).scalars()
        )
        return to_out(row, devices, await self._known(row.site_id, devices))

    async def latest(self, site_id: UUID) -> sc.ScanOut:
        await self._site(site_id)
        stmt = (
            select(DiscoveryScan)
            .where(
                DiscoveryScan.site_id == site_id,
                DiscoveryScan.created_at > _now() - RETENTION,
            )
            .order_by(DiscoveryScan.created_at.desc())
            .limit(1)
        )
        return await self._read(stmt)

    async def get(self, site_id: UUID, scan_id: UUID) -> sc.ScanOut:
        await self._site(site_id)
        return await self._read(
            select(DiscoveryScan).where(
                DiscoveryScan.id == scan_id, DiscoveryScan.site_id == site_id
            )
        )

    async def _known(self, site_id: UUID, devices: list[DiscoveryDevice]) -> dict[UUID, UUID]:
        """device.id -> screen.id, d'après les propriétés annotées `x-vds-device-address`.

        Le noyau ne connaît aucune clé de pilote : tout vient du schéma de config du module.
        """
        fields = self._fields()
        if not devices or not fields:
            return {}
        by_ip = {_host(d.ip): d.id for d in devices}
        by_mac = {_mac_key(d.mac): d.id for d in devices if d.mac}
        screens = (
            await self._s.execute(
                select(Screen.id).where(Screen.site_id == site_id, Screen.archived_at.is_(None))
            )
        ).scalars()
        svc = ConfigService(self._s, self._schemas)
        out: dict[UUID, UUID] = {}
        for screen_id in list(screens):
            for ns, keys in fields.items():
                try:
                    res = await svc.resolve("screen", screen_id, ns)
                except Exception:  # noqa: BLE001 - config d'un pilote illisible : ignoré
                    log.warning("discovery.known_config_unreadable", extra={"namespace": ns})
                    continue
                for key, kind in keys.items():
                    rk = res.values.get(key)
                    value = rk.value if rk is not None else None
                    if not isinstance(value, str) or not value:
                        continue
                    hit = by_ip.get(value.strip()) if kind == "ip" else by_mac.get(_mac_key(value))
                    if hit is not None:
                        out.setdefault(hit, screen_id)
        return out


class DiscoveryEvents:
    """Abonné du bus : lots d'appareils relayés par l'agent et résultat final de la commande."""

    def __init__(self, maker: async_sessionmaker[AsyncSession], bus: EventBus) -> None:
        self._maker, self._bus = maker, bus

    def register(self, subscribe: Callable[[str, Any], Any]) -> None:
        subscribe(BATCH_EVENT, self.on_batch)

    async def on_batch(self, event: Event) -> None:
        p = event.payload
        try:
            scan_id = UUID(str(p["scan_id"]))
            raw = p["devices"]
        except (KeyError, ValueError):
            return
        if not isinstance(raw, list):
            return
        async with self._maker() as s:
            row = (
                await s.execute(
                    select(DiscoveryScan).where(DiscoveryScan.id == scan_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.status not in ACTIVE or event.origin != f"agent:{row.site_id}":
                log.warning("discovery.batch_ignored", extra={"scan_id": str(scan_id)})
                return
            current = (
                await s.execute(
                    select(SiteAgent.id).where(
                        SiteAgent.site_id == row.site_id, SiteAgent.revoked_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if current is None or current != row.agent_id:  # autre agent que celui du scan
                log.warning("discovery.batch_wrong_agent", extra={"scan_id": str(scan_id)})
                return
            known = {
                _host(ip)
                for ip in (
                    await s.execute(
                        select(DiscoveryDevice.ip).where(DiscoveryDevice.scan_id == scan_id)
                    )
                ).scalars()
            }
            for item in raw[:MAX_BATCH]:
                try:
                    dev = DiscoveredDevice.model_validate(item)
                except ValidationError:
                    continue
                if not _private(dev.ip):
                    continue
                ip = str(dev.ip)
                if ip not in known and len(known) >= MAX_DEVICES:
                    row.capped = True
                    continue
                known.add(ip)
                values = {
                    "scan_id": scan_id,
                    "ip": ip,
                    "mac": dev.mac,
                    "vendor": dev.vendor,
                    "model": dev.model,
                    "name": dev.name,
                    "platform": dev.platform,
                    "protocols": sorted(dev.protocols),
                    "suggested_drivers": list(dev.suggested_drivers),
                    "confidence": dev.confidence,
                }
                stmt = pg_insert(DiscoveryDevice).values(**values)
                await s.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_discovery_device_scan_ip",
                        set_={k: stmt.excluded[k] for k in values if k not in ("scan_id", "ip")},
                    )
                )
            if row.status == "pending":
                row.status = "running"
            await s.commit()

    async def on_cmd_result(
        self, cmd_id: UUID, status: str, code: str | None, data: dict[str, Any]
    ) -> None:
        async with self._maker() as s:
            row = (
                await s.execute(
                    select(DiscoveryScan).where(DiscoveryScan.cmd_id == cmd_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.status not in ACTIVE:
                return
            events: list[Event]
            if status == "ok":
                events = await self._complete(s, row, data)
            else:
                if status == "expired":
                    fail = "discovery.timeout"
                elif code == "unknown_command":
                    fail = "discovery.agent_outdated"
                else:
                    fail = _clean_code(code, "discovery.internal")
                events = await finish(s, row, "failed", fail)
            await s.commit()
        await _publish(self._bus, events)

    async def _complete(
        self, s: AsyncSession, row: DiscoveryScan, data: dict[str, Any]
    ) -> list[Event]:
        try:
            result = DiscoveryResult.model_validate(data)
        except ValidationError:
            return await finish(s, row, "failed", "discovery.internal")
        if result.scan_id != row.id or not _valid_networks(list(result.networks)):
            return await finish(s, row, "failed", "discovery.internal")
        nets = list(result.networks)
        row.networks = [str(n) for n in nets]
        row.truncated = result.truncated
        row.capped = row.capped or result.capped
        outside = [
            d.id
            for d in (
                await s.execute(select(DiscoveryDevice).where(DiscoveryDevice.scan_id == row.id))
            ).scalars()
            if not any(ipaddress.ip_address(_host(d.ip)) in n for n in nets)
        ]
        if outside:  # hors des réseaux déclarés par l'agent : rejeté
            await s.execute(delete(DiscoveryDevice).where(DiscoveryDevice.id.in_(outside)))
        return await finish(s, row, "completed", duration_ms=result.duration_ms)
