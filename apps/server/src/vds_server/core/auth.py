"""Pipeline d'authentification : requête -> AuthProvider actif -> Identity -> Principal.

Aucune connaissance d'un provider précis : tout passe par l'interface `AuthProvider`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import AccessDenied, Event
from vds_sdk.server import AuthProvider, AuthRequest

from vds_server.core import users
from vds_server.core.errors import ApiError
from vds_server.events import CoreBus

Role = Literal["admin", "editor"]
RANK: dict[str, int] = {"editor": 1, "admin": 2}


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    provider: str
    email: str | None
    display_name: str
    role: Role
    locale: str | None

    @property
    def actor_id(self) -> str:
        return str(self.user_id)


def to_auth_request(request: Request) -> AuthRequest:
    return AuthRequest(
        method=request.method,
        path=request.url.path,
        headers={k.lower(): v for k, v in request.headers.items()},
        cookies=dict(request.cookies),
        query=dict(request.query_params),
    )


class AuthPipeline:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        bus: CoreBus,
        provider: Callable[[], AuthProvider | None],
    ) -> None:
        self._maker = maker
        self._bus = bus
        self._provider = provider

    def provider(self) -> AuthProvider:
        p = self._provider()
        if p is None:
            raise ApiError("auth.provider_unavailable", 503)
        return p

    async def resolve(self, request: Request) -> Principal:
        provider = self.provider()
        try:
            identity = await provider.authenticate(to_auth_request(request))
            if identity is None:
                raise ApiError("auth.unauthenticated", 401)
            async with self._maker() as session:
                row, created = await users.upsert_identity(session, identity)
                await session.commit()
        except AccessDenied as exc:
            raise ApiError(exc.code, 403) from exc
        if created:
            await self._bus.publish(
                Event(name="user.created", payload={"id": str(row.id)}, source="core")
            )
        return Principal(
            user_id=row.id,
            provider=row.auth_provider,
            email=row.email,
            display_name=row.display_name,
            role="admin" if row.role == "admin" else "editor",
            locale=row.locale,
        )

    async def principal(self, request: Request) -> Principal:
        """Dépendance FastAPI : 401 sans identité, 403 si le provider refuse."""
        cached: Principal | None = request.scope.get("vds.principal")
        if cached is None:
            cached = await self.resolve(request)
            request.scope["vds.principal"] = cached
        return cached

    def require(self, role: Role) -> Callable[[Request], Awaitable[Principal]]:
        """Dépendance : rôle minimal requis (admin > editor)."""

        async def dependency(request: Request) -> Principal:
            principal = await self.principal(request)
            if RANK[principal.role] < RANK[role]:
                raise ApiError("auth.forbidden", 403, required=role)
            return principal

        return dependency
