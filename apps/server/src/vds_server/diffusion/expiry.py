"""Expiration des médias et playlists : prédicats Python et SQL communs (instant UTC absolu)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core.errors import ApiError
from vds_server.db.models import Organization


def aware(value: datetime | None) -> datetime | None:
    """Instant UTC : une date sans fuseau est refusée (422), jamais interprétée."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ApiError("expiry.timezone_required", 422)
    return value.astimezone(UTC)


def is_expired(row: Any, now: datetime) -> bool:
    expires_at = row.expires_at
    return row.expired_at is not None or (expires_at is not None and expires_at <= now)


def expired_clause(model: Any, now: datetime) -> ColumnElement[bool]:
    return or_(
        model.expired_at.is_not(None),
        and_(model.expires_at.is_not(None), model.expires_at <= now),
    )


def state_clause(model: Any, state: str, now: datetime, soon_days: int) -> ColumnElement[bool]:
    if state == "all":
        return true()
    expired = expired_clause(model, now)
    if state == "expired":
        return expired
    if state == "active":
        return ~expired
    if state == "expiring":
        horizon = now + timedelta(days=soon_days)
        return and_(~expired, model.expires_at.is_not(None), model.expires_at <= horizon)
    return false()


def apply_expiry(row: Any, new: datetime | None, now: datetime) -> bool:
    """Pose `expires_at`. Retourne True si c'est une restauration (objet expiré prolongé)."""
    row.expires_at = new
    restored = row.expired_at is not None and (new is None or new > now)
    if restored:
        row.expired_at = None
    return restored


DEFAULT_EXPIRING_SOON_DAYS = 7


async def expiring_soon_days(session: AsyncSession, schemas: ConfigSchemaProvider | None) -> int:
    """`core.media.expiring_soon_days` (clé d'organisation)."""
    if schemas is None:
        return DEFAULT_EXPIRING_SOON_DAYS
    org = await session.scalar(select(Organization.id))
    if org is None:
        return DEFAULT_EXPIRING_SOON_DAYS
    res = await ConfigService(session, schemas).resolve("organization", org, CORE_NAMESPACE)
    return int(res.values["media.expiring_soon_days"].value)
