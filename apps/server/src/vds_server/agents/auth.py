"""Jeton d'agent `vds_agt_…` : génération, vérification (HTTP et WebSocket)."""

import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vds_server.core.errors import ApiError
from vds_server.db.models_power import SiteAgent
from vds_server.player.device_auth import bearer, hash_secret

TOKEN_PREFIX = "vds_agt_"  # noqa: S105
PREFIX_LEN = 8


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_prefix(token: str) -> str:
    return token[:PREFIX_LEN]


@dataclass(frozen=True)
class AgentPrincipal:
    agent_id: UUID
    site_id: UUID
    token_hash: str


async def lookup(session: AsyncSession, token: str | None) -> SiteAgent | None:
    """Agent portant ce jeton (révoqué inclus), ou None."""
    if token is None or not token.startswith(TOKEN_PREFIX):
        return None
    stmt = select(SiteAgent).where(SiteAgent.token_hash == hash_secret(token))
    return (await session.execute(stmt)).scalar_one_or_none()


class AgentAuth:
    """`Depends(rt.agent_auth)` -> `AgentPrincipal` (401 errors.agent_unauthorized|agent_revoked)"""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def __call__(self, request: Request) -> AgentPrincipal:
        async with self._maker() as session:
            row = await lookup(session, bearer(request))
        if row is None:
            raise ApiError("errors.agent_unauthorized", 401)
        if row.revoked_at is not None:
            raise ApiError("errors.agent_revoked", 401)
        return AgentPrincipal(row.id, row.site_id, row.token_hash)
