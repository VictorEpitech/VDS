"""Enrôlement d'agent : routes admin (codes) et route publique d'échange `agent/enroll`."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk.agent.enroll import EnrollRequest

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.db.models_power import SiteAgent
from vds_server.enrollment import schemas as sc
from vds_server.enrollment.service import EnrollmentService, to_out
from vds_server.security.client_ip import client_ip

COUNTED_FAILURES = frozenset({401, 409, 410})


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["enrollment"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    def svc(session: Session) -> EnrollmentService:
        return EnrollmentService(session, rt.bus, rt.settings)

    Svc = Annotated[EnrollmentService, Depends(svc)]

    @router.post(
        "/sites/{site_id}/agent/enrollments", response_model=sc.EnrollmentCreated, status_code=201
    )
    async def create(
        site_id: UUID, body: sc.EnrollmentCreate, s: Svc, actor: Admin
    ) -> sc.EnrollmentCreated:
        if not rt.settings.public_origin:  # avant toute écriture : rien à nettoyer
            raise ApiError("agent.public_origin_missing", 409)
        row, code = await s.create(site_id, actor, ttl_min=body.ttl_min)
        return s.created_view(row, code)

    @router.get("/sites/{site_id}/agent/enrollments", response_model=list[sc.EnrollmentOut])
    async def list_enrollments(site_id: UUID, s: Svc, _a: Admin) -> list[sc.EnrollmentOut]:
        return [to_out(r) for r in await s.list_for_site(site_id)]

    @router.delete("/sites/{site_id}/agent/enrollments/{enrollment_id}", status_code=204)
    async def revoke(site_id: UUID, enrollment_id: UUID, s: Svc, actor: Admin) -> Response:
        await s.revoke(site_id, enrollment_id, actor)
        return Response(status_code=204)

    @router.get("/agent/local", response_model=sc.LocalAgentOut)
    async def local_agent(session: Session, _a: Admin) -> sc.LocalAgentOut:
        detected = await rt.local_channel.read_presence()
        site_id = None
        if detected is not None and detected.agent_id is not None:
            agent = await session.get(SiteAgent, detected.agent_id)
            if agent is not None and agent.revoked_at is None:
                site_id = agent.site_id
        return sc.LocalAgentOut(
            available=rt.local_channel.available, detected=detected, site_id=site_id
        )

    @router.post("/sites/{site_id}/agent/local", response_model=sc.LocalActivated, status_code=202)
    async def activate_local(site_id: UUID, s: Svc, actor: Admin) -> sc.LocalActivated:
        if not rt.local_channel.available:
            raise ApiError("agent.local.unavailable", 409)
        if await rt.local_channel.read_presence() is None:
            raise ApiError("agent.local.not_detected", 409)
        row, code = await s.create(site_id, actor, channel="local")
        try:
            await rt.local_channel.write_handoff(code, row.id, row.expires_at)
        except OSError as exc:
            await s.revoke(site_id, row.id, actor)  # pas de code orphelin
            raise ApiError("agent.local.write_failed", 502) from exc
        return sc.LocalActivated(enrollment_id=row.id, expires_at=row.expires_at)

    @router.post("/agent/enroll", response_model=sc.EnrollOut)
    async def enroll(request: Request, body: EnrollRequest, s: Svc) -> sc.EnrollOut:
        # Un agent n'est pas un navigateur : tout Origin est refusé (comme /ws/agent).
        if "origin" in request.headers:
            raise ApiError("errors.origin_mismatch", 403)
        key = client_ip(request)
        rt.enroll_limiter.check(key)
        try:
            return await s.consume(body, request.client.host if request.client else None)
        except ApiError as exc:
            if exc.status in COUNTED_FAILURES:
                rt.enroll_limiter.fail(key)
            raise

    return router
