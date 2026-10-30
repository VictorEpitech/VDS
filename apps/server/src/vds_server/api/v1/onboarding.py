"""Premiers pas : GET (éditeur) ; PUT étapes ignorées / carte masquée (admin, audité)."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime
from vds_server.onboarding import service
from vds_server.onboarding.schemas import CardIn, OnboardingOut, StepSkipIn
from vds_server.supervision.status import Thresholds


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])
    Session = Annotated[AsyncSession, Depends(rt.session)]
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    async def current(session: AsyncSession, org_id: UUID) -> OnboardingOut:
        th = Thresholds.from_config(await rt.core_config())
        return await service.compute(session, org_id, th, datetime.now(UTC))

    @router.get("", response_model=OnboardingOut)
    async def get_onboarding(session: Session, _: Reader) -> OnboardingOut:
        return await current(session, await service.organization_id(session))

    @router.put("/steps/{step}", response_model=OnboardingOut)
    async def put_step(
        step: str, body: StepSkipIn, session: Session, actor: Admin
    ) -> OnboardingOut:
        org_id = await service.set_step_skipped(session, step, body.skipped, actor)
        await session.commit()
        return await current(session, org_id)

    @router.put("/card", response_model=OnboardingOut)
    async def put_card(body: CardIn, session: Session, actor: Admin) -> OnboardingOut:
        org_id = await service.set_card_hidden(session, body.hidden, actor)
        await session.commit()
        return await current(session, org_id)

    return router
