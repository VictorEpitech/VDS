"""Registre des modules exposé à l'admin (schémas et traductions lus à chaud, rien au build)."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from vds_sdk import ContractError

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
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


class ModuleSettingsOut(BaseModel):
    id: str
    version: str
    name: dict[str, str]
    description: dict[str, str]
    config_schema: dict[str, Any]
    i18n: dict[str, dict[str, Any]]


class ModuleStateIn(BaseModel):
    state: Literal["enabled", "disabled"]


class ModuleStateOut(BaseModel):
    id: str
    state: str
    last_error: str | None


def build_router(rt: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/registry", tags=["registry"])
    Reader = Annotated[Principal, Depends(rt.pipeline.require("editor"))]
    Admin = Annotated[Principal, Depends(rt.pipeline.require("admin"))]

    @router.get("/content-types", response_model=list[ContentTypeOut])
    async def list_content_types(_: Reader) -> list[ContentTypeOut]:
        return [ContentTypeOut.model_validate(c) for c in rt.registry.content_types()]

    @router.get("/modules/{module_id}/settings", response_model=ModuleSettingsOut)
    async def module_settings(module_id: str, _: Reader) -> ModuleSettingsOut:
        entry = rt.registry.settings_entry(module_id)
        if entry is None:
            raise ApiError("registry.module_not_found", 404)
        return ModuleSettingsOut.model_validate(entry)

    @router.put("/modules/{module_id}/state", response_model=ModuleStateOut)
    async def set_module_state(module_id: str, body: ModuleStateIn, actor: Admin) -> ModuleStateOut:
        try:
            if body.state == "enabled":
                await rt.registry.enable(module_id)
            else:
                await rt.registry.disable(module_id)
        except ContractError as exc:
            status = 404 if exc.code == "module.not_found" else 409
            raise ApiError(exc.code, status) from exc
        row = await rt.registry.get(module_id)
        if row is None:
            raise ApiError("module.not_found", 404)
        async with rt.maker.begin() as s:
            audit.record(
                s,
                actor_kind="user",
                actor_id=actor.actor_id,
                action=f"module.{body.state}",
                target_type="module",
                target_id=module_id,
            )
        return ModuleStateOut(id=row.id, state=row.state, last_error=row.last_error)

    return router
