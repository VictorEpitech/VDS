"""Routes `/api/v1/config/...` (fines : tout passe par ConfigService)."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk.bus import EventBus

from vds_server.config.resolver import Origin, Resolution
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.config.service import ConfigError, ConfigService

Level = Literal["organization", "site", "group", "screen"]


class OriginOut(BaseModel):
    level: Literal["organization", "site", "group", "screen", "default", "env"]
    id: UUID | None = None
    name: str | None = None


class ConfigValueOut(BaseModel):
    value: Any
    origin: OriginOut
    inherited: Any
    inherited_origin: OriginOut
    overridable: bool
    secret: bool = False
    is_set: bool | None = None  # renseigné pour les secrets uniquement (valeur jamais renvoyée)


class ConfigWarningOut(BaseModel):
    code: str
    key: str
    level: str | None = None
    scope_id: UUID | None = None


class ConfigOut(BaseModel):
    values: dict[str, ConfigValueOut]
    warnings: list[ConfigWarningOut]


class ConfigValueIn(BaseModel):
    value: Any


class ErrorOut(BaseModel):
    code: str
    params: dict[str, Any] = {}


@dataclass
class ConfigDeps:
    """Dépendances injectées par l'application (session, schémas, bus, identité)."""

    session: Callable[..., AsyncIterator[AsyncSession]]
    schemas: ConfigSchemaProvider
    bus: EventBus | None = None
    actor_id: Callable[..., UUID | None] = lambda: None
    read_guard: Callable[..., Any] | None = None  # dépendance exigée pour lire (ex. rôle éditeur)
    write_guard: Callable[..., Any] | None = None  # dépendance exigée pour écrire (ex. admin)


def _origin(o: Origin) -> OriginOut:
    return OriginOut(level=o.level, id=o.id, name=o.name)  # type: ignore[arg-type]


def _out(r: Resolution) -> ConfigOut:
    return ConfigOut(
        values={
            k: ConfigValueOut(
                value=v.value,
                origin=_origin(v.origin),
                inherited=v.inherited,
                inherited_origin=_origin(v.inherited_origin),
                overridable=v.overridable,
                secret=v.secret,
                is_set=v.is_set if v.secret else None,
            )
            for k, v in r.values.items()
        },
        warnings=[ConfigWarningOut(**vars(w)) for w in r.warnings],
    )


class _ConfigRoute(APIRoute):
    """Traduit ConfigError en réponse `{code, params}` (codes stables, l'admin traduit)."""

    def get_route_handler(self) -> Callable[[Request], Any]:
        handler = super().get_route_handler()

        async def wrapped(request: Request) -> Response:
            try:
                return await handler(request)
            except ConfigError as exc:
                return JSONResponse(
                    status_code=exc.status,
                    content=ErrorOut(code=exc.code, params=exc.params).model_dump(),
                )

        return wrapped


def build_router(deps: ConfigDeps) -> APIRouter:
    read_deps = [Depends(deps.read_guard)] if deps.read_guard else []
    write_deps = [Depends(deps.write_guard)] if deps.write_guard else []
    router = APIRouter(
        prefix="/api/v1/config", tags=["config"], route_class=_ConfigRoute, dependencies=read_deps
    )

    async def service(
        session: Annotated[AsyncSession, Depends(deps.session)],
    ) -> ConfigService:
        return ConfigService(session, deps.schemas, deps.bus)

    Svc = Annotated[ConfigService, Depends(service)]
    Actor = Annotated[UUID | None, Depends(deps.actor_id)]

    @router.get("/{level}/{target_id}/{namespace}", response_model=ConfigOut)
    async def read(level: Level, target_id: UUID, namespace: str, svc: Svc) -> ConfigOut:
        return _out(await svc.resolve(level, target_id, namespace))

    @router.put("/{level}/{target_id}/{namespace}/{key}", status_code=204, dependencies=write_deps)
    async def put(
        level: Level,
        target_id: UUID,
        namespace: str,
        key: str,
        body: ConfigValueIn,
        svc: Svc,
        actor: Actor,
    ) -> Response:
        await svc.set_override(level, target_id, namespace, key, body.value, actor)
        return Response(status_code=204)

    @router.delete(
        "/{level}/{target_id}/{namespace}/{key}", status_code=204, dependencies=write_deps
    )
    async def delete(
        level: Level, target_id: UUID, namespace: str, key: str, svc: Svc, actor: Actor
    ) -> Response:
        await svc.delete_override(level, target_id, namespace, key, actor)
        return Response(status_code=204)

    return router
