"""Routes admin des agents de site : création, rotation, révocation, commandes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.agents import schemas as sc
from vds_server.agents.service import AgentService, to_out
from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/sites/{site_id}/agents", tags=["agents"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def svc(session: Session, actor: Admin) -> AgentService:
        return AgentService(session, rt.bus, actor, rt.command_queue)

    Svc = Annotated[AgentService, Depends(svc)]

    @router.get("", response_model=list[sc.AgentOut])
    async def list_agents(site_id: UUID, s: Svc) -> list[sc.AgentOut]:
        return [to_out(a) for a in await s.list_for_site(site_id)]

    @router.post("", response_model=sc.AgentWithToken, status_code=201)
    async def create(site_id: UUID, body: sc.AgentCreate, s: Svc) -> sc.AgentWithToken:
        row, token = await s.create(site_id, body)
        return sc.AgentWithToken(agent=to_out(row), token=token)

    @router.post("/{agent_id}/rotate", response_model=sc.AgentWithToken)
    async def rotate(site_id: UUID, agent_id: UUID, s: Svc) -> sc.AgentWithToken:
        row, token = await s.rotate(site_id, agent_id)
        return sc.AgentWithToken(agent=to_out(row), token=token)

    @router.post("/{agent_id}/revoke", response_model=sc.AgentOut)
    async def revoke(site_id: UUID, agent_id: UUID, s: Svc) -> sc.AgentOut:
        return to_out(await s.revoke(site_id, agent_id))

    @router.get("/{agent_id}/commands", response_model=list[sc.CommandOut])
    async def commands(site_id: UUID, agent_id: UUID, s: Svc) -> list[sc.CommandOut]:
        return [sc.CommandOut.model_validate(c) for c in await s.commands(site_id, agent_id)]

    @router.post("/{agent_id}/commands", response_model=sc.CommandOut, status_code=202)
    async def send_command(
        site_id: UUID, agent_id: UUID, body: sc.CommandCreate, s: Svc
    ) -> sc.CommandOut:
        return sc.CommandOut.model_validate(await s.send_command(site_id, agent_id, body))

    return router
