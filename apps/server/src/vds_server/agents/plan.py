"""Plan d'alimentation de l'agent : `GET agent/plan` (ETag/304) et poussée `plan.updated`."""

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event

from vds_server.agents.auth import AgentPrincipal
from vds_server.agents.queue import CommandQueue
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.db.models import Screen, ScreenGroup, Site
from vds_server.db.models_power import RemoteCommand, SiteAgent
from vds_server.power.plan import PowerPlanBuilder

if TYPE_CHECKING:
    from vds_server.core.runtime import Runtime

log = logging.getLogger("vds.agents")

# Événements qui peuvent modifier le plan d'un site (portée résolue en sites concernés).
TRIGGERS = (
    "power.plan_changed",
    "override.created",
    "override.updated",
    "override.stopped",
    "config.changed",
    "screen.created",
    "screen.updated",
    "screen.deleted",
    "screen.paired",
    "screen.unpaired",
)


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    tags = [t.strip().removeprefix("W/") for t in header.split(",")]
    return "*" in tags or etag in tags


class PlanPusher:
    """Abonné du bus : pousse `plan.updated` aux agents des sites dont la révision change."""

    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        schemas: ConfigSchemaProvider,
        queue: CommandQueue,
    ) -> None:
        self._maker = maker
        self._schemas = schemas
        self._queue = queue

    def register(self, subscribe: Any) -> None:
        for name in TRIGGERS:
            subscribe(name, self._on_event)

    async def _sites(self, s: AsyncSession, payload: dict[str, Any]) -> set[UUID]:
        level = payload.get("scope_level")
        raw = payload.get("scope_id") or payload.get("screen_id") or payload.get("id")
        if "override_id" in payload:  # forçage : la cible est un niveau de la chaîne
            level, raw = payload.get("target_type"), payload.get("target_id")
        try:
            target = UUID(str(raw)) if raw else None
        except ValueError:
            return set()
        if level is None and "scope_level" not in payload and "override_id" not in payload:
            # événement d'écran
            site = await s.scalar(select(Screen.site_id).where(Screen.id == target))
            if site is None and payload.get(
                "site_id"
            ):  # écran supprimé : le site vient de l'événement
                try:
                    site = UUID(str(payload["site_id"]))
                except ValueError:
                    return set()
            return {site} if site else set()
        if level == "site" and target:
            return {target} if await s.get(Site, target) else set()
        if level == "group" and target:
            site = await s.scalar(select(ScreenGroup.site_id).where(ScreenGroup.id == target))
            return {site} if site else set()
        if level == "screen" and target:
            site = await s.scalar(select(Screen.site_id).where(Screen.id == target))
            return {site} if site else set()
        # organisation : tous les sites
        return set((await s.execute(select(SiteAgent.site_id))).scalars())

    async def _on_event(self, event: Event) -> None:
        try:
            await self._push(event.payload)
        except Exception:
            log.exception("agent.plan_push_failed")

    async def _last_pushed(self, agent_id: UUID) -> str | None:
        """Dernière révision poussée, lue en base : cohérent entre workers (pas d'état mémoire)."""
        async with self._maker() as s:
            args = await s.scalar(
                select(RemoteCommand.args)
                .where(
                    RemoteCommand.target_kind == "agent",
                    RemoteCommand.target_id == agent_id,
                    RemoteCommand.name == "plan.updated",
                )
                .order_by(RemoteCommand.seq.desc())
                .limit(1)
            )
        return str(args.get("revision")) if isinstance(args, dict) else None

    async def _push(self, payload: dict[str, Any]) -> None:
        async with self._maker() as s:
            sites = await self._sites(s, payload)
            if not sites:
                return
            agents = (
                await s.execute(
                    select(SiteAgent.id, SiteAgent.site_id).where(
                        SiteAgent.site_id.in_(sites), SiteAgent.revoked_at.is_(None)
                    )
                )
            ).all()
            plans = {
                site: (await PowerPlanBuilder(s, self._schemas).agent_plan(site))["revision"]
                for site in {a.site_id for a in agents}
            }
        for agent_id, site_id in agents:
            revision = str(plans[site_id])
            if await self._last_pushed(agent_id) == revision:
                continue
            await self._queue.enqueue(
                "agent", agent_id, "plan.updated", args={"revision": revision}
            )


def build_router(rt: "Runtime") -> APIRouter:
    router = APIRouter(tags=["agent-plan"])

    @router.get("/api/v1/agent/plan", responses={304: {"description": "Plan inchangé"}})
    async def agent_plan(
        request: Request,
        principal: AgentPrincipal = Depends(rt.agent_auth),  # noqa: B008
    ) -> Response:
        async with rt.maker() as s:
            body = await PowerPlanBuilder(s, rt.schemas).agent_plan(principal.site_id)
        etag = f'"{body["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)
        return Response(json.dumps(body), media_type="application/json", headers=headers)

    return router
