"""Gestion des agents par l'admin : création, rotation, révocation, commandes."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus
from vds_sdk.wire import Cmd

from vds_server.agents import schemas as sc
from vds_server.agents.auth import new_token, token_prefix
from vds_server.agents.queue import CommandQueue, QueuedCommand, notify
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen, Site
from vds_server.db.models_power import RemoteCommand, SiteAgent
from vds_server.player.device_auth import hash_secret
from vds_server.provisioning.tickets import ARGS_KEY, UNUSABLE_TICKET, TicketBox

OFFLINE_AFTER_S = 180  # 3 heartbeats manqués
SCREEN_COMMANDS = frozenset({"power.set", "power.probe"})


def agent_status(row: SiteAgent, now: datetime | None = None) -> sc.AgentStatus:
    if row.revoked_at is not None:
        return "revoked"
    if row.last_seen_at is None:
        return "unseen"
    now = now or datetime.now(UTC)
    return "online" if now - row.last_seen_at < timedelta(seconds=OFFLINE_AFTER_S) else "offline"


def to_out(row: SiteAgent) -> sc.AgentOut:
    hb = row.last_heartbeat
    return sc.AgentOut(
        id=row.id,
        site_id=row.site_id,
        name=row.name,
        token_prefix=row.token_prefix,
        status=agent_status(row),
        version=row.version,
        last_seen_at=row.last_seen_at,
        last_heartbeat=hb if isinstance(hb, dict) else None,
        created_at=row.created_at,
        rotated_at=row.rotated_at,
        revoked_at=row.revoked_at,
    )


class AgentService:
    def __init__(
        self, session: AsyncSession, bus: EventBus | None, actor: Principal, queue: CommandQueue
    ) -> None:
        self._s = session
        self._bus = bus
        self._actor = actor
        self._queue = queue

    def _audit(self, action: str, row: SiteAgent) -> Event:
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action=action,
            target_type="site_agent",
            target_id=str(row.id),
            details={"site_id": str(row.site_id)},
        )
        return Event(
            name=action,
            payload={"agent_id": str(row.id), "site_id": str(row.site_id)},
            source="core",
        )

    async def _agent(self, site_id: UUID, agent_id: UUID) -> SiteAgent:
        row = await self._s.get(SiteAgent, agent_id)
        if row is None or row.site_id != site_id:
            raise ApiError("agents.not_found", 404)
        return row

    async def _commit(self, event: Event | None, agent: SiteAgent | None = None) -> None:
        try:
            if agent is not None:  # réveille la session : elle se ferme si jeton changé/révoqué
                await notify(self._s, "agent", agent.id)
            await self._s.commit()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("agents.already_active", 409) from exc
        if event is not None and self._bus is not None:
            await self._bus.publish(event)

    async def list_for_site(self, site_id: UUID) -> list[SiteAgent]:
        await self._site(site_id)
        stmt = select(SiteAgent).where(SiteAgent.site_id == site_id).order_by(SiteAgent.created_at)
        return list((await self._s.execute(stmt)).scalars())

    async def _site(self, site_id: UUID) -> Site:
        site = await self._s.get(Site, site_id)
        if site is None:
            raise ApiError("site.not_found", 404)
        return site

    async def create(self, site_id: UUID, body: sc.AgentCreate) -> tuple[SiteAgent, str]:
        site = await self._site(site_id)
        if site.archived_at is not None:
            raise ApiError("site.archived", 409)
        token = new_token()
        row = SiteAgent(
            site_id=site_id,
            name=body.name,
            token_hash=hash_secret(token),
            token_prefix=token_prefix(token),
            enrolled_by=self._actor.user_id,
        )
        self._s.add(row)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("agents.already_active", 409) from exc
        await self._commit(self._audit("agent.enrolled", row))
        return row, token

    async def rotate(self, site_id: UUID, agent_id: UUID) -> tuple[SiteAgent, str]:
        row = await self._agent(site_id, agent_id)
        if row.revoked_at is not None:
            raise ApiError("agents.revoked", 409)
        token = new_token()
        row.token_hash, row.token_prefix = hash_secret(token), token_prefix(token)
        row.rotated_at = datetime.now(UTC)
        await self._commit(self._audit("agent.rotated", row), row)
        return row, token

    async def revoke(self, site_id: UUID, agent_id: UUID) -> SiteAgent:
        row = await self._agent(site_id, agent_id)
        if row.revoked_at is not None:
            return row  # idempotent
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = self._actor.user_id
        await self._commit(self._audit("agent.revoked", row), row)
        return row

    async def commands(self, site_id: UUID, agent_id: UUID) -> list[RemoteCommand]:
        await self._agent(site_id, agent_id)
        return await self._queue.recent("agent", agent_id)

    async def send_command(
        self, site_id: UUID, agent_id: UUID, body: sc.CommandCreate
    ) -> RemoteCommand:
        row = await self._agent(site_id, agent_id)
        if row.revoked_at is not None:
            raise ApiError("agents.revoked", 409)
        if body.name in INTERNAL_AGENT_COMMANDS:
            raise ApiError("agents.command_internal", 422)
        validate_command(body.name, body.args, body.screen_id)
        if body.screen_id is not None:
            screen = await self._s.get(Screen, body.screen_id)
            if screen is None or screen.site_id != site_id:
                raise ApiError("agents.screen_not_in_site", 403)
        elif body.name in SCREEN_COMMANDS:
            raise ApiError("agents.command_screen_required", 422)
        cmd = await self._queue.enqueue(
            "agent",
            agent_id,
            body.name,
            screen_id=body.screen_id,
            args=body.args,
            ttl_s=body.ttl_s,
            created_by=self._actor.user_id,
        )
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="agent.command",
            target_type="site_agent",
            target_id=str(agent_id),
            details={"name": body.name, "cmd_id": str(cmd.id)},
        )
        await self._s.commit()
        return cmd


# Réservées au noyau : seule la session de provisioning (provisioning/service.py) les émet.
INTERNAL_AGENT_COMMANDS = frozenset({"screen.provision"})


def validate_command(name: str, args: dict[str, Any], screen_id: UUID | None) -> None:
    """Liste fermée de commandes agent et schéma des arguments (vds_sdk.wire)."""
    probe = Cmd(
        cmd_id=UUID(int=0),
        name=name,
        screen_id=screen_id,
        args=args,
        expires_at=datetime.now(UTC),
    )
    try:
        probe.validate_for("agent")
    except ValidationError as exc:
        raise ApiError("agents.command_args_invalid", 422) from exc
    except ValueError as exc:
        raise ApiError("agents.command_unknown", 422) from exc


def command_frame_data(cmd: QueuedCommand, box: TicketBox | None = None) -> dict[str, Any]:
    args = cmd.args
    if cmd.name == "screen.provision" and ARGS_KEY in args:
        # le ticket n'est en clair que sur le fil : déchiffré à l'envoi, jamais stocké en clair
        args = {k: v for k, v in args.items() if k != ARGS_KEY}
        ticket = box.decrypt(str(cmd.args[ARGS_KEY])) if box is not None else None
        args["pairing_ticket"] = ticket or UNUSABLE_TICKET
    return Cmd(
        cmd_id=cmd.id,
        name=cmd.name,
        screen_id=cmd.screen_id,
        args=args,
        expires_at=cmd.expires_at,
        correlation_id=str(cmd.correlation_id) if cmd.correlation_id else None,
    ).model_dump(mode="json", exclude_none=True)
