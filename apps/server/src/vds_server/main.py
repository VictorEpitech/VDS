"""Point d'entrée ASGI. Tout est monté sous VDS_BASE_PATH."""

import os
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.agents import cache as agent_cache
from vds_server.agents import plan as agent_plan
from vds_server.api.v1 import (
    agents,
    diffusion,
    discovery,
    enrollment,
    fleet,
    health,
    incidents,
    inventory,
    kiosk,
    layouts,
    me,
    media_files,
    onboarding,
    overrides,
    planning,
    player,
    power,
    provisioning,
    registry,
)
from vds_server.config.api import ConfigDeps
from vds_server.config.api import build_router as build_config_router
from vds_server.config.reader import ModuleConfigReader
from vds_server.config.schemas import CORE_NAMESPACE
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError, install_error_handlers
from vds_server.core.runtime import Runtime
from vds_server.diffusion.scope import PerimeterScope
from vds_server.middleware.headers import SecurityHeadersMiddleware
from vds_server.middleware.same_origin import SameOriginMiddleware
from vds_server.middleware.upload_limit import UploadLimitMiddleware
from vds_server.security.cookies import warn_if_insecure_http
from vds_server.settings import Settings
from vds_server.web import spa
from vds_server.ws import agent as agent_ws
from vds_server.ws import player as player_ws

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _auth_info(rt: Runtime) -> dict[str, Any]:
    """URL de connexion/déconnexion et liens d'enveloppe, fournis par le provider actif."""
    provider = rt.auth_provider()
    if provider is None:
        return {"loginUrl": None, "logoutUrl": None, "links": []}
    return {
        "loginUrl": provider.login_url(rt.settings.url("/")),
        "logoutUrl": provider.logout_url(),
        "links": [
            {"id": link.id, "labelKey": link.label_key, "href": link.url}
            for link in provider.shell_links()
        ],
    }


def create_app(
    settings: Settings | None = None,
    extra_routers: Sequence[APIRouter] = (),
    lifespan: Lifespan | None = None,
    runtime: Runtime | None = None,
) -> FastAPI:
    """Sans `runtime` : application minimale (santé, SPA) ; avec : noyau complet."""
    settings = settings or Settings()
    if runtime is not None and lifespan is None:
        lifespan = runtime.lifespan
    inner = FastAPI(title="VDS")
    install_error_handlers(inner)
    inner.include_router(health.router)
    for router in extra_routers:
        inner.include_router(router)
    auth_info: spa.AuthInfo = spa.no_auth
    if runtime is not None:
        pipeline = runtime.pipeline
        inner.include_router(me.build_router(runtime))
        inner.include_router(inventory.build_router(runtime))
        inner.include_router(diffusion.build_router(runtime))
        inner.include_router(layouts.build_router(runtime))
        inner.include_router(overrides.build_router(runtime))
        inner.include_router(planning.build_router(runtime))
        inner.include_router(media_files.build_router(runtime))
        inner.include_router(power.build_router(runtime))
        inner.include_router(player.build_router(runtime))
        inner.include_router(kiosk.build_router(runtime))
        inner.include_router(registry.build_router(runtime))
        inner.include_router(agents.build_router(runtime))
        inner.include_router(enrollment.build_router(runtime))
        inner.include_router(incidents.build_router(runtime))
        inner.include_router(provisioning.build_router(runtime))
        inner.include_router(discovery.build_router(runtime))
        inner.include_router(onboarding.build_router(runtime))
        inner.include_router(fleet.build_router(runtime))
        inner.include_router(agent_ws.build_router(runtime))
        inner.include_router(agent_cache.build_router(runtime))
        inner.include_router(agent_plan.build_router(runtime))
        inner.include_router(player_ws.build_router(runtime))
        inner.include_router(
            build_config_router(
                ConfigDeps(
                    session=runtime.session,
                    schemas=runtime.schemas,
                    bus=runtime.bus,
                    actor_id=_actor_id(runtime),
                    read_guard=_config_read_guard(runtime),
                    write_guard=pipeline.require("admin"),
                )
            )
        )
        inner.mount("/api/v1/m", runtime.mux.router)

        def auth_info() -> dict[str, Any]:
            return _auth_info(runtime)

    inner.include_router(spa.build_router(settings, auth_info))
    root = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if settings.prefix:
        root.mount(settings.prefix, inner)
    else:
        root.mount("/", inner)
    if runtime is not None:
        root.add_middleware(UploadLimitMiddleware, settings=settings, limit=_upload_limit(runtime))
    root.add_middleware(SameOriginMiddleware, settings=settings)
    root.add_middleware(SecurityHeadersMiddleware)
    return root


def _upload_limit(rt: Runtime) -> Callable[[], Awaitable[int]]:
    async def limit() -> int:
        cfg = await ModuleConfigReader(rt.maker, rt.schemas, CORE_NAMESPACE).resolve()
        return int(cfg.get("media_max_upload_mb", 2048)) * 1024 * 1024

    return limit


def _actor_id(rt: Runtime) -> Callable[..., Any]:
    async def actor_id(p: Annotated[Principal, Depends(rt.pipeline.principal)]) -> UUID:
        return p.user_id

    return actor_id


def _config_read_guard(rt: Runtime) -> Callable[..., Any]:
    """Lecture/écriture de configuration : la cible doit être dans le périmètre (404 sinon)."""

    async def guard(
        p: Annotated[Principal, Depends(rt.pipeline.require("editor"))],
        session: Annotated[AsyncSession, Depends(rt.session)],
        level: str,
        target_id: UUID,
    ) -> None:
        if level == "organization" and not p.perimeter.empty:
            return
        if not await PerimeterScope(session).can_read_target(p, level, target_id):
            raise ApiError("config.target_not_found", 404)

    return guard


def app_factory() -> FastAPI:
    if "*" in os.environ.get("FORWARDED_ALLOW_IPS", ""):
        # un proxy « de confiance » universel permettrait de forger l'IP cliente (limiteurs)
        raise RuntimeError("errors.forwarded_allow_ips_wildcard")
    settings = Settings()
    warn_if_insecure_http(settings)
    return create_app(settings, runtime=Runtime(settings))
