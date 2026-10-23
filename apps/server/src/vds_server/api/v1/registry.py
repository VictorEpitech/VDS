"""Registre des modules exposé à l'admin (schémas et traductions lus à chaud, rien au build)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from vds_server.core.auth import Principal
from vds_server.core.runtime import Runtime


class ContentTypeOut(BaseModel):
    id: str
    version: str
    name: dict[str, str]
    description: dict[str, str]
    element: str
    accepts: list[str]
    requires_network: bool
    item_schema: dict[str, Any]
    config_schema: dict[str, Any] | None
    i18n: dict[str, dict[str, Any]]


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/registry", tags=["registry"])
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]

    @router.get("/content-types", response_model=list[ContentTypeOut])
    async def list_content_types(_: Reader) -> list[ContentTypeOut]:
        return [ContentTypeOut.model_validate(c) for c in rt.registry.content_types()]

    return router
