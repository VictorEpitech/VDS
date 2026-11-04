"""Relance de l'application VDS (vds-core 1.14.0) : disponibilité, choix de l'exécutant, résultat.

Deux exécutants : le player (commande `restart{scope:"app"}` du lot I) ou l'agent de site
(commande d'agent fermée `app.restart`, ADB). Le noyau ne connaît pas la plateforme : il route
vers le module déclarant `app.restart.<plateforme>`. Aucune branche selon le mode d'hébergement.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus
from vds_sdk.agent.provision import AppRestartOutcome, AppRestartRequest
from vds_sdk.versions import Version

from vds_server.agents.service import agent_status
from vds_server.commands import service as svc
from vds_server.db.models import ScreenDevice
from vds_server.db.models_command import PlayerCommand
from vds_server.db.models_power import RemoteCommand, SiteAgent
from vds_server.inventory import schemas as isc
from vds_server.provisioning.apk_update import agent_supports

log = logging.getLogger("vds.restart_app")

CMD_NAME = "app.restart"
CMD_TTL_S = 30  # délai de la commande d'agent
ROW_TTL_S = 120  # pas de relance surprise tardive
MIN_CORE = Version(1, 14, 0)
PLAYER_ONLINE_S = 90
PLATFORM = "android"
CAPABILITY = "cmd.restart.app"
FAILED_CODE = "app.restart.failed"

_CODE_RE = re.compile(r"[a-z0-9_.]{1,96}")

AppRestartModules = Callable[[], list[tuple[str, str, frozenset[str]]]]
HostOf = Callable[[UUID], Awaitable[Any]]


@dataclass(frozen=True)
class RestartContext:
    """Dépendances du calcul de disponibilité (modules actifs du registre, adresse ADB)."""

    modules: AppRestartModules
    host_of: HostOf


def player_online(dev: ScreenDevice | None, now: datetime) -> bool:
    return (
        dev is not None
        and dev.revoked_at is None
        and dev.last_seen_at is not None
        and now - dev.last_seen_at <= timedelta(seconds=PLAYER_ONLINE_S)
    )


def availability(
    *,
    dev: ScreenDevice | None,
    now: datetime,
    agent: SiteAgent | None,
    modules: list[tuple[str, str, frozenset[str]]],
    host: object,
) -> isc.RestartAppAvailability:
    """Fonction pure : exécutants possibles maintenant et, sinon, première raison bloquante."""
    if dev is None or dev.revoked_at is not None:
        return isc.RestartAppAvailability(available=False, via=[], reason="no_capability")
    caps = svc.capabilities_of(dev.last_heartbeat)
    has_cap = CAPABILITY in caps
    via: list[Literal["player", "agent"]] = []
    if has_cap and player_online(dev, now):
        via.append("player")
    agent_reason: isc.RestartAppReason | None = None
    if dev.platform == PLATFORM:
        if agent is None or agent_status(agent, now) != "online":
            agent_reason = "agent_offline"
        else:
            support = agent_supports(agent.last_hello, modules, PLATFORM, MIN_CORE)
            if support != "ok":
                agent_reason = support
            elif not (isinstance(host, str) and host.strip()):
                agent_reason = "no_adb_host"
        if agent_reason is None:
            via.append("agent")
    if via:
        return isc.RestartAppAvailability(available=True, via=via, reason=None)
    if dev.platform == PLATFORM and agent_reason is not None:
        reason: isc.RestartAppReason = agent_reason
    elif has_cap:
        reason = "player_offline"
    else:
        reason = "no_capability"
    return isc.RestartAppAvailability(available=False, via=[], reason=reason)


async def availability_many(
    session: AsyncSession,
    ctx: RestartContext,
    screens: list[Any],
    devices: dict[UUID, Any],
    now: datetime,
) -> dict[UUID, isc.RestartAppAvailability]:
    """Une requête d'agents pour tous les sites ; modules en cache ; adresse seulement si utile."""
    site_ids = {s.site_id for s in screens}
    agents: dict[UUID, SiteAgent] = {}
    if site_ids:
        stmt = select(SiteAgent).where(
            SiteAgent.site_id.in_(site_ids), SiteAgent.revoked_at.is_(None)
        )
        agents = {a.site_id: a for a in (await session.execute(stmt)).scalars()}
    modules = ctx.modules()
    out: dict[UUID, isc.RestartAppAvailability] = {}
    for s in screens:
        dev = devices.get(s.id)
        agent = agents.get(s.site_id)
        host: object = None
        if (
            dev is not None
            and dev.revoked_at is None
            and dev.platform == PLATFORM
            and agent is not None
            and agent_status(agent, now) == "online"
            and agent_supports(agent.last_hello, modules, PLATFORM, MIN_CORE) == "ok"
        ):
            host = await ctx.host_of(s.id)
        out[s.id] = availability(dev=dev, now=now, agent=agent, modules=modules, host=host)
    return out


def agent_args(command_id: UUID, screen_id: UUID) -> dict[str, Any]:
    return AppRestartRequest(
        request_id=command_id, screen_id=screen_id, platform=PLATFORM
    ).model_dump(mode="json")


class RestartAppEvents:
    """Résultat de la commande d'agent `app.restart` : statut de la ligne `player_command`."""

    def __init__(self, maker: async_sessionmaker[AsyncSession], bus: EventBus) -> None:
        self._maker, self._bus = maker, bus

    async def on_cmd_result(
        self, cmd_id: UUID, status: str, code: str | None, data: dict[str, Any]
    ) -> None:
        events: list[Event] = []
        async with self._maker() as s:
            cmd = await s.get(RemoteCommand, cmd_id)
            if cmd is None or cmd.name != CMD_NAME:
                return
            try:
                req = AppRestartRequest.model_validate(cmd.args)
            except ValidationError:
                return
            row = (
                await s.execute(
                    select(PlayerCommand)
                    .where(PlayerCommand.id == req.request_id, PlayerCommand.executor == "agent")
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None or row.status not in svc.PENDING:
                return
            outcome: AppRestartOutcome | None = None
            if status == "ok":
                try:
                    outcome = AppRestartOutcome.model_validate(data)
                except ValidationError:
                    outcome = None  # sortie invalide de l'agent = échec
            ok = outcome is not None and outcome.ok
            fail = (outcome.code if outcome else None) or code
            if not (isinstance(fail, str) and _CODE_RE.fullmatch(fail)):
                fail = FAILED_CODE
            events.append(
                svc.terminate(s, row, "ok" if ok else "failed", None if ok else fail,
                              now=datetime.now(UTC))
            )  # fmt: skip
            await s.commit()
        for event in events:
            await self._bus.publish(event)
