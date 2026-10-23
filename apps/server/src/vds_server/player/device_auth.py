"""Jeton d'appareil `vds_dev_…` : génération, hachage, dépendance FastAPI réutilisable.

Point d'accroche commun aux routes `/api/v1/player/*` et au service des médias (`<base>/media/*`).
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from vds_server.core.errors import ApiError
from vds_server.db.models import ScreenDevice

TOKEN_PREFIX = "vds_dev_"  # noqa: S105


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bearer_value(header: str) -> str | None:
    scheme, _, value = header.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def bearer(request: Request) -> str | None:
    return bearer_value(request.headers.get("authorization", ""))


@dataclass(frozen=True)
class DevicePrincipal:
    device_id: UUID
    screen_id: UUID
    platform: str


class DeviceAuth:
    """`Depends(rt.device_auth)` -> `DevicePrincipal`.

    401 `errors.device_unauthorized` (absent/inconnu) ou `errors.device_revoked`
    (révoqué ou expiré : le player efface tout et revient à l'appairage).
    """

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = maker

    async def __call__(self, request: Request) -> DevicePrincipal:
        token = bearer(request)
        if token is None or not token.startswith(TOKEN_PREFIX):
            raise ApiError("errors.device_unauthorized", 401)
        async with self._maker() as session:
            row = (
                await session.execute(
                    select(ScreenDevice).where(ScreenDevice.token_hash == hash_secret(token))
                )
            ).scalar_one_or_none()
        if row is None:
            raise ApiError("errors.device_unauthorized", 401)
        now = datetime.now(UTC)
        if row.revoked_at is not None or (row.expires_at is not None and row.expires_at <= now):
            raise ApiError("errors.device_revoked", 401)
        return DevicePrincipal(row.id, row.screen_id, row.platform)
