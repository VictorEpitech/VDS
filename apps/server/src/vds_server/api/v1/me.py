"""GET /api/v1/me : identité courante (jamais d'info technique de session)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime


class MeOut(BaseModel):
    id: UUID
    display_name: str
    email: str | None
    role: str
    provider: str
    locale: str | None


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["me"])

    @router.get("/me", response_model=MeOut)
    async def me(p: Annotated[Principal, Depends(rt.pipeline.principal)]) -> MeOut:
        return MeOut(
            id=p.user_id,
            display_name=p.display_name,
            email=p.email,
            role=p.role,
            provider=p.provider,
            locale=p.locale,
        )

    return router
