"""Lecture et acquittement des incidents (supervision)."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.core.runtime import Runtime
from vds_server.db.models_power import Incident
from vds_server.supervision import incidents as inc
from vds_server.supervision.schemas import IncidentOut


async def _out(session: AsyncSession, row: Incident) -> IncidentOut:
    v = await inc.view(session, row)
    names = v["subject"]
    return IncidentOut(
        id=row.id,
        kind=row.kind,
        severity=row.severity,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        subject_name=names.get(row.subject_type),
        site_id=v["site_id"],
        site_name=v["site_name"],
        details=row.details,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        acknowledged_at=row.acknowledged_at,
        acknowledged_by=row.acknowledged_by,
    )


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    @router.get("", response_model=list[IncidentOut])
    async def list_incidents(
        session: Session,
        _: Reader,
        state: Literal["open", "resolved", "all"] = "open",
        kind: Annotated[str | None, Query(max_length=80)] = None,
        site_id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[IncidentOut]:
        stmt = select(Incident).order_by(Incident.opened_at.desc(), Incident.id)
        if state == "open":
            stmt = stmt.where(Incident.resolved_at.is_(None))
        elif state == "resolved":
            stmt = stmt.where(Incident.resolved_at.is_not(None))
        if kind:
            stmt = stmt.where(Incident.kind == kind)
        if site_id:
            stmt = stmt.where(Incident.site_id == site_id)
        rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        return [await _out(session, r) for r in rows]

    @router.get("/{incident_id}", response_model=IncidentOut)
    async def get_incident(incident_id: UUID, session: Session, _: Reader) -> IncidentOut:
        row = await session.get(Incident, incident_id)
        if row is None:
            raise ApiError("incidents.not_found", 404)
        return await _out(session, row)

    @router.post("/{incident_id}/acknowledge", response_model=IncidentOut)
    async def acknowledge(incident_id: UUID, session: Session, actor: Admin) -> IncidentOut:
        row = await session.get(Incident, incident_id, with_for_update=True)
        if row is None:
            raise ApiError("incidents.not_found", 404)
        if row.resolved_at is not None:
            raise ApiError("incidents.already_resolved", 409)
        if row.acknowledged_at is None:  # idempotent : le premier acquittement est conservé
            row.acknowledged_at = datetime.now(UTC)
            row.acknowledged_by = actor.user_id
            audit.record(
                session,
                actor_kind="user",
                actor_id=str(actor.user_id),
                action="incident.acknowledged",
                target_type="incident",
                target_id=str(row.id),
                details={"kind": row.kind},
            )
            await session.commit()
        return await _out(session, row)

    return router
