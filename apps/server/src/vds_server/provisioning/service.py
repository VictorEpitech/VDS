"""Sessions de provisioning d'écran : création, reprise, annulation, suivi, validation.

Le noyau ignore la plateforme : il émet une commande fermée `screen.provision` vers l'agent du
site, suit les étapes rapportées par l'agent et passe la session à `done` quand l'écran du
ticket est appairé et a envoyé son premier heartbeat.
"""

import ipaddress
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, get_args
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.agent.provision import ProvisionOutcome, ProvisionStepId, parse_lan_address
from vds_sdk.versions import Version
from vds_sdk.wire import ScreenProvisionArgs

from vds_server.agents.queue import CommandQueue
from vds_server.agents.service import agent_status
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen, ScreenDevice
from vds_server.db.models_power import SiteAgent
from vds_server.db.models_provision import ACTIVE, ScreenProvision
from vds_server.provisioning import schemas as sc
from vds_server.provisioning.apk import ApkCatalog, ApkEntry
from vds_server.provisioning.tickets import (
    ARGS_KEY,
    TICKET_TTL_S,
    TicketBox,
    new_ticket,
    ticket_hash,
)
from vds_server.settings import Settings

log = logging.getLogger("vds.provisioning")

STEP_ORDER: tuple[str, ...] = get_args(ProvisionStepId)
MIN_CORE = Version(1, 3, 0)
COMMAND_TTL_S = 300  # expires_at = création + 5 min (contrôlé avant démarrage)
ONLINE_TIMEOUT = timedelta(minutes=5)  # appairé mais jamais en ligne (court : fenêtre d'usurpation)
CMD_NAME = "screen.provision"

# (id, version, plateformes) des modules site-agent actifs qui déclarent `provision.<plateforme>`
ProvisionModules = Callable[[], list[tuple[str, str, frozenset[str]]]]


def _now() -> datetime:
    return datetime.now(UTC)


def _kiosk(row: ScreenProvision) -> str:
    outcome = row.outcome if isinstance(row.outcome, dict) else {}
    if outcome.get("kiosk") in ("device_owner", "soft", "none"):
        return str(outcome["kiosk"])
    steps = row.steps or {}
    for step, kind in (("device_owner", "device_owner"), ("soft_kiosk", "soft")):
        if (steps.get(step) or {}).get("status") == "ok":
            return kind
    return "none"


def _failed_step(row: ScreenProvision) -> str:
    for step in STEP_ORDER:
        if ((row.steps or {}).get(step) or {}).get("status") == "failed":
            return step
    return "unknown"


def _inject_ok(row: ScreenProvision) -> bool:
    return ((row.steps or {}).get("inject") or {}).get("status") == "ok"


async def finish(
    session: AsyncSession,
    row: ScreenProvision,
    status: str,
    code: str | None = None,
    *,
    audit_finished: bool = True,
) -> list[Event]:
    """Clôt la session : ticket invalidé, chiffré effacé de la commande, audit, événement."""
    row.status = status
    row.code = code
    row.finished_at = _now()
    row.ticket_hash = None
    row.ticket_expires_at = None
    if row.cmd_id is not None:
        await session.execute(
            text("UPDATE remote_command SET args = args - :k WHERE id = :id"),
            {"k": ARGS_KEY, "id": row.cmd_id},
        )
    kiosk = _kiosk(row)
    if audit_finished:
        audit.record(
            session,
            actor_kind="system",
            actor_id=None,
            action="screen.provision.finished",
            target_type="screen",
            target_id=str(row.screen_id),
            details={
                "provision_id": str(row.id),
                "status": status,
                "kiosk": kiosk,
                "code": code,
            },
        )
    base = {"provision_id": str(row.id), "screen_id": str(row.screen_id)}
    if status == "done":
        payload = {**base, "kiosk": kiosk, "version_code": row.apk_version_code}
        return [Event(name="screen.provisioned", payload=payload, source="core")]
    if status == "failed":
        payload = {**base, "step": _failed_step(row), "code": code}
        return [Event(name="screen.provision_failed", payload=payload, source="core")]
    return []


async def _publish(bus: EventBus | None, events: list[Event]) -> None:
    if bus is not None:
        for event in events:
            await bus.publish(event)


def to_out(row: ScreenProvision) -> sc.ProvisionOut:
    steps: list[sc.StepOut] = []
    for step in STEP_ORDER:
        info = (row.steps or {}).get(step)
        if isinstance(info, dict):
            at = info.get("at")
            steps.append(
                sc.StepOut(
                    step=step,  # type: ignore[arg-type]
                    status=info["status"],
                    code=info.get("code"),
                    at=datetime.fromisoformat(at) if at else None,
                )
            )
    outcome = None
    if isinstance(row.outcome, dict):
        try:
            outcome = sc.OutcomeOut.model_validate(
                ProvisionOutcome.model_validate(row.outcome).model_dump()
            )
        except ValidationError:
            outcome = None
    return sc.ProvisionOut(
        id=row.id,
        screen_id=row.screen_id,
        site_id=row.site_id,
        platform=row.platform,
        address=str(row.address),
        port=row.port,
        status=row.status,  # type: ignore[arg-type]
        steps=steps,
        outcome=outcome,
        code=row.code,
        apk_version_code=row.apk_version_code,
        ticket_expires_at=row.ticket_expires_at,
        created_at=row.created_at,
        finished_at=row.finished_at,
        non_public_ack=row.non_public_ack_at is not None,
        non_public_ack_at=row.non_public_ack_at,
        pairing_code_fallback=row.paired_at is None,
    )


class ProvisionService:
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        queue: CommandQueue,
        settings: Settings,
        catalog: ApkCatalog,
        box: TicketBox | None,
        modules: ProvisionModules,
    ) -> None:
        self._s, self._bus, self._actor, self._queue = session, bus, actor, queue
        self._settings, self._catalog, self._box, self._modules = settings, catalog, box, modules

    # --- capacités ----------------------------------------------------------
    async def _agent(self, site_id: UUID) -> SiteAgent | None:
        stmt = select(SiteAgent).where(SiteAgent.site_id == site_id, SiteAgent.revoked_at.is_(None))
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def capabilities(self, site_id: UUID) -> sc.CapabilitiesOut:
        apk = self._catalog.latest()
        info = (
            sc.ApkInfo(version_name=apk.version_name, version_code=apk.version_code)
            if apk
            else None
        )

        def no(reason: sc.Reason) -> sc.CapabilitiesOut:
            return sc.CapabilitiesOut(available=False, reason=reason, platforms=[], apk=info)

        agent = await self._agent(site_id)
        if agent is None or agent_status(agent) != "online":
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
        platforms: set[str] = set()
        for mid, version, plats in modules:
            try:
                if mid in reported and Version.parse(str(reported[mid])) >= Version.parse(version):
                    platforms |= plats
            except ValueError:
                continue
        if not platforms:
            return no("agent_outdated")
        if self._box is None or not self._settings.public_origin:
            return no("server_unconfigured")
        if apk is None:
            return no("apk_unavailable")
        return sc.CapabilitiesOut(
            available=True, reason=None, platforms=sorted(platforms), apk=info
        )

    # --- lecture / expiration paresseuse --------------------------------------
    async def _row(self, pid: UUID, *, lock: bool = False) -> ScreenProvision:
        stmt = select(ScreenProvision).where(ScreenProvision.id == pid)
        row = (await self._s.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
        if row is None:
            raise ApiError("provision.not_found", 404)
        return row

    async def _expire_stale(self, row: ScreenProvision) -> None:
        if row.status not in ACTIVE:
            return
        now = _now()
        code: str | None = None
        if row.paired_at is None and row.ticket_expires_at and row.ticket_expires_at <= now:
            code = "provision.ticket_expired"
        elif row.paired_at is not None and now - row.paired_at > ONLINE_TIMEOUT:
            code = "provision.online_timeout"
        if code is not None:
            events = await finish(self._s, row, "failed", code)
            await self._s.commit()
            await _publish(self._bus, events)

    async def get(self, pid: UUID) -> ScreenProvision:
        row = await self._row(pid, lock=True)
        await self._expire_stale(row)
        await self._s.commit()
        return row

    # --- création / reprise -------------------------------------------------------
    async def create(self, screen_id: UUID, body: sc.ProvisionCreate) -> ScreenProvision:
        screen = await self._s.get(Screen, screen_id)
        if screen is None or screen.archived_at is not None:
            raise ApiError("errors.screen_not_found", 404)
        address = _lan(body.address)
        caps = await self.capabilities(screen.site_id)
        if not caps.available or body.platform not in caps.platforms:
            raise ApiError("provision.unavailable", 409, reason=caps.reason or "platform")
        agent = await self._agent(screen.site_id)
        apk = self._catalog.latest()
        if agent is None or apk is None:  # garanti par capabilities, repli défensif
            raise ApiError("provision.unavailable", 409, reason="agent_offline")
        if not body.reprovision:
            paired = (
                await self._s.execute(
                    select(ScreenDevice.id).where(
                        ScreenDevice.screen_id == screen_id, ScreenDevice.revoked_at.is_(None)
                    )
                )
            ).first()
            if paired is not None:
                raise ApiError("provision.screen_paired", 409)
        for stale in (
            await self._s.execute(
                select(ScreenProvision)
                .where(ScreenProvision.screen_id == screen_id, ScreenProvision.status.in_(ACTIVE))
                .with_for_update()
            )
        ).scalars():
            await self._expire_stale(stale)
        await self._s.flush()
        row = ScreenProvision(
            id=uuid4(),
            screen_id=screen_id,
            site_id=screen.site_id,
            agent_id=agent.id,
            platform=body.platform,
            address=address,
            port=body.port,
            status="queued",
            steps={},
            apk_version_code=apk.version_code,
            created_by=self._actor.user_id,
        )
        if body.non_public_ack:
            row.non_public_ack_at, row.non_public_ack_by = _now(), self._actor.user_id
            self._record_ack(row, screen_id)
        self._s.add(row)
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="screen.provision.started",
            target_type="screen",
            target_id=str(screen_id),
            details={
                "provision_id": str(row.id),
                "site_id": str(screen.site_id),
                "address": address,
                "platform": body.platform,
                "version_code": apk.version_code,
            },
        )
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("provision.session_active", 409) from exc
        await self._dispatch(
            row, apk, device_owner=body.device_owner, soft=body.soft_kiosk_fallback, from_step=None
        )
        await _publish(
            self._bus,
            [
                Event(
                    name="screen.provision_started",
                    payload={
                        "provision_id": str(row.id),
                        "screen_id": str(screen_id),
                        "site_id": str(screen.site_id),
                        "platform": body.platform,
                        "actor_id": self._actor.actor_id,
                    },
                    source="core",
                )
            ],
        )
        return row

    async def _dispatch(
        self,
        row: ScreenProvision,
        apk: ApkEntry,
        *,
        device_owner: str,
        soft: bool,
        from_step: str | None,
    ) -> None:
        """Émet ticket + commande ; la ligne est validée avant l'enqueue (id connu de la file)."""
        if self._box is None or not self._settings.public_origin:
            raise ApiError("provision.unavailable", 409, reason="server_unconfigured")
        ticket = new_ticket()
        server_url = self._settings.public_origin.rstrip("/") + self._settings.prefix
        args = {
            "provision_id": str(row.id),
            "platform": row.platform,
            "address": str(row.address),
            "port": row.port,
            "apk": apk.ref().model_dump(),
            "server_url": server_url,
            "pairing_ticket": ticket,
            "device_owner": device_owner,
            "soft_kiosk_fallback": soft,
            "from_step": from_step,
            "non_public_ack": row.non_public_ack_at is not None,
        }
        try:
            ScreenProvisionArgs.model_validate(args)
        except ValidationError as exc:
            await self._s.rollback()
            raise ApiError("provision.args_invalid", 422) from exc
        stored = {k: v for k, v in args.items() if k != "pairing_ticket" and v is not None}
        stored[ARGS_KEY] = self._box.encrypt(ticket)
        row.ticket_hash = ticket_hash(ticket)
        row.ticket_expires_at = _now() + timedelta(seconds=TICKET_TTL_S)
        row.apk_version_code = apk.version_code
        await self._s.commit()
        try:
            cmd = await self._queue.enqueue(
                "agent",
                row.agent_id,
                CMD_NAME,
                screen_id=row.screen_id,
                args=stored,
                ttl_s=COMMAND_TTL_S,
                created_by=self._actor.user_id,
            )
        except Exception:
            events = await finish(self._s, row, "failed", "provision.enqueue_failed")
            await self._s.commit()
            await _publish(self._bus, events)
            raise
        row.cmd_id = cmd.id
        await self._s.commit()

    async def retry(self, pid: UUID, body: sc.ProvisionRetry) -> ScreenProvision:
        row = await self._row(pid, lock=True)
        if row.status != "failed":
            raise ApiError("provision.not_retryable", 409)
        caps = await self.capabilities(row.site_id)
        if not caps.available or row.platform not in caps.platforms:
            raise ApiError("provision.unavailable", 409, reason=caps.reason or "platform")
        agent = await self._agent(row.site_id)
        apk = self._catalog.get(row.apk_version_code or 0) or self._catalog.latest()
        if agent is None or apk is None:
            raise ApiError("provision.unavailable", 409, reason="agent_offline")
        cut = STEP_ORDER.index(body.from_step) if body.from_step is not None else 0
        row.steps = {k: v for k, v in (row.steps or {}).items() if STEP_ORDER.index(k) < cut}
        row.agent_id = agent.id
        row.status, row.code, row.outcome = "queued", None, None
        row.finished_at = row.paired_at = row.online_at = None
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="screen.provision.retried",
            target_type="screen",
            target_id=str(row.screen_id),
            details={"provision_id": str(row.id), "from_step": body.from_step},
        )
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("provision.session_active", 409) from exc
        await self._dispatch(row, apk, device_owner="try", soft=True, from_step=body.from_step)
        return row

    def _record_ack(self, row: ScreenProvision, screen_id: UUID) -> None:
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="screen.provision.non_public_acked",
            target_type="screen",
            target_id=str(screen_id),
            details={"provision_id": str(row.id)},
        )

    async def ack_non_public(self, pid: UUID) -> ScreenProvision:
        """Confirme « écran non public » : pris en compte à la prochaine émission (reprise)."""
        row = await self._row(pid, lock=True)
        if row.status == "cancelled":
            raise ApiError("provision.not_ackable", 409)
        if row.non_public_ack_at is None:
            row.non_public_ack_at, row.non_public_ack_by = _now(), self._actor.user_id
            self._record_ack(row, row.screen_id)
            await self._s.commit()
        return row

    async def cancel(self, pid: UUID) -> ScreenProvision:
        row = await self._row(pid, lock=True)
        if row.status not in ACTIVE:
            raise ApiError("provision.not_cancellable", 409)
        if row.cmd_id is not None:
            await self._s.execute(
                text(
                    "UPDATE remote_command SET status = 'expired', code = 'provision.cancelled',"
                    " done_at = now() WHERE id = :id AND status IN ('queued','sent')"
                ),
                {"id": row.cmd_id},
            )
        await finish(self._s, row, "cancelled", "provision.cancelled", audit_finished=False)
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="screen.provision.cancelled",
            target_type="screen",
            target_id=str(row.screen_id),
            details={"provision_id": str(row.id)},
        )
        await self._s.commit()
        return row


def _lan(value: str) -> str:
    try:
        return parse_lan_address(value)
    except ValueError as exc:
        raise ApiError(str(exc), 422) from exc


# --- consommation du ticket (appairage) et validation (heartbeat) -------------------------
def lan_client_allowed(client: str, address: object) -> bool:
    """Un client d'adresse privée (serveur sur le LAN du site) doit être la TV provisionnée.

    Les adresses publiques (serveur distant, NAT du site) ne sont pas vérifiables ici ; la
    boucle locale est exemptée (proxy local). Limite : docs/architecture-provision-android.md.
    """
    try:
        ip = ipaddress.ip_address(client.split("%")[0])
        expected = ipaddress.ip_address(str(address).split("/")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or not (ip.is_private or ip.is_link_local):
        return True
    return ip == expected


async def consume_ticket(
    session: AsyncSession, ticket: str, client: str | None = None, *, lan_check: bool = True
) -> ScreenProvision:
    """Ticket valide, non expiré, non consommé -> session verrouillée, marquée appairée.

    Un client hors du réseau de la session est rejeté sans consommer le ticket (pas de déni).
    """
    stmt = (
        select(ScreenProvision)
        .where(ScreenProvision.ticket_hash == ticket_hash(ticket))
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    now = _now()
    if (
        row is None
        or row.status not in ACTIVE
        or row.paired_at is not None
        or row.ticket_expires_at is None
        or row.ticket_expires_at <= now
    ):
        raise ApiError("errors.pairing_ticket_invalid", 401)
    if lan_check and client is not None and not lan_client_allowed(client, row.address):
        raise ApiError("errors.pairing_ticket_invalid", 401)
    row.paired_at = now
    row.ticket_hash = None  # usage unique
    row.status = "waiting_online"
    return row


async def mark_online(session: AsyncSession, screen_id: UUID, bus: EventBus | None) -> None:
    """Premier heartbeat d'un écran appairé par ticket -> session `done`."""
    stmt = (
        select(ScreenProvision)
        .where(
            ScreenProvision.screen_id == screen_id,
            ScreenProvision.paired_at.is_not(None),
            ScreenProvision.online_at.is_(None),
            ScreenProvision.status.in_(ACTIVE),
        )
        .with_for_update()
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return
    row.online_at = _now()
    events = await finish(session, row, "done")
    await session.commit()
    await _publish(bus, events)


class ProvisionEvents:
    """Abonnés du bus : progression relayée par l'agent et résultat final de la commande."""

    def __init__(self, maker: async_sessionmaker[AsyncSession], bus: EventBus) -> None:
        self._maker, self._bus = maker, bus

    def register(self, subscribe: Callable[[str, Any], Any]) -> None:
        subscribe("screen.provision_progressed", self.on_progressed)

    async def on_progressed(self, event: Event) -> None:
        p = event.payload
        try:
            pid, sid = UUID(str(p["provision_id"])), UUID(str(p["screen_id"]))
            step, status = str(p["step"]), str(p["status"])
        except (KeyError, ValueError):
            return
        if step not in STEP_ORDER or status not in (
            "pending",
            "running",
            "waiting_user",
            "ok",
            "skipped",
            "failed",
        ):
            return
        code = p.get("code")
        async with self._maker() as s:
            row = (
                await s.execute(
                    select(ScreenProvision).where(ScreenProvision.id == pid).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.screen_id != sid or event.origin != f"agent:{row.site_id}":
                return
            if row.status not in ACTIVE:
                return
            steps = dict(row.steps or {})
            steps[step] = {
                "status": status,
                "code": code if isinstance(code, str) else None,
                "at": _now().isoformat(),
            }
            row.steps = steps
            if row.status in ("queued", "running", "waiting_user"):
                row.status = "waiting_user" if status == "waiting_user" else "running"
            await s.commit()

    async def on_cmd_result(
        self, cmd_id: UUID, status: str, code: str | None, data: dict[str, Any]
    ) -> None:
        async with self._maker() as s:
            row = (
                await s.execute(
                    select(ScreenProvision)
                    .where(ScreenProvision.cmd_id == cmd_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return
            events: list[Event] = []
            if row.status in ACTIVE:
                outcome: ProvisionOutcome | None = None
                if status == "ok":
                    try:
                        outcome = ProvisionOutcome.model_validate(data)
                    except ValidationError:
                        outcome = None
                if outcome is not None:
                    row.outcome = outcome.model_dump(mode="json")
                if outcome is not None and outcome.ok:
                    pass  # validation par heartbeat de l'écran appairé
                elif _inject_ok(row):
                    # Revue I2 : ADB peut se couper après `inject` (Device Owner). Le ticket reste
                    # valide ; l'expiration du ticket et `online_timeout` tranchent.
                    row.outcome = None
                else:
                    fail = (outcome.code if outcome else None) or code or "provision.failed"
                    events = await finish(s, row, "failed", fail)
            elif row.status == "done" and status == "ok":
                row.outcome = data or row.outcome
            await s.commit()
        await _publish(self._bus, events)
