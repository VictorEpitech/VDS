"""Kiosque géré : mode servi au player (pur), chargement par lot, maintenance temporaire."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk.bus import Event, EventBus

from vds_server.config.schemas import CORE_NAMESPACE
from vds_server.config.service import upsert_override
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import ConfigOverride, Screen

KIOSK_KEY = "player.kiosk"
UNTIL_KEY = "player.kiosk_maintenance_until"
MIN_MAINTENANCE_MIN = 5
MAX_MAINTENANCE_MIN = 240

KioskMode = Literal["locked", "unlocked", "maintenance"]
Enforcement = Literal["lock_task", "soft", "web"]


@dataclass(frozen=True)
class KioskRequest:
    """Valeurs de config résolues pour un écran (défaut : verrouillé, pas de maintenance)."""

    mode: Literal["locked", "unlocked"] = "locked"
    until: datetime | None = None


def parse_until(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def format_until(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def served(request: KioskRequest, now: datetime) -> dict[str, Any]:
    """Fonction pure : maintenance active > mode configuré ; maintenance échue = ignorée."""
    if request.until is not None and request.until > now:
        return {"mode": "maintenance", "until": format_until(request.until)}
    return {"mode": request.mode, "until": None}


async def load_requests(
    session: AsyncSession, screens: Iterable[Screen]
) -> dict[UUID, KioskRequest]:
    """Résolution par lot : écran > groupe > défaut (organisation/site ignorés, non permis)."""
    rows = list(screens)
    if not rows:
        return {}
    ids = {r.id for r in rows} | {r.group_id for r in rows if r.group_id is not None}
    found = (
        await session.execute(
            select(ConfigOverride.scope_id, ConfigOverride.key, ConfigOverride.value).where(
                ConfigOverride.namespace == CORE_NAMESPACE,
                ConfigOverride.key.in_([KIOSK_KEY, UNTIL_KEY]),
                ConfigOverride.scope_id.in_(ids),
            )
        )
    ).all()
    stored = {(scope, key): value for scope, key, value in found}
    out: dict[UUID, KioskRequest] = {}
    for r in rows:
        mode: Literal["locked", "unlocked"] = "locked"
        for scope in (r.id, r.group_id):
            value = stored.get((scope, KIOSK_KEY))
            if value in ("locked", "unlocked"):
                mode = value
                break
        out[r.id] = KioskRequest(mode, parse_until(stored.get((r.id, UNTIL_KEY))))
    return out


class KioskService:
    def __init__(self, session: AsyncSession, bus: EventBus | None) -> None:
        self._s = session
        self._bus = bus

    async def _screen(self, screen_id: UUID) -> Screen:
        screen = await self._s.get(Screen, screen_id)
        if screen is None or screen.archived_at is not None:
            raise ApiError("errors.screen_not_found", 404)
        return screen

    async def _publish(self, screen_id: UUID) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                name="config.changed",
                payload={
                    "namespace": CORE_NAMESPACE,
                    "keys": [UNTIL_KEY],
                    "scope_level": "screen",
                    "scope_id": str(screen_id),
                },
                source="core",
            )
        )

    async def start_maintenance(
        self, actor: Principal, screen_id: UUID, duration_min: int, reason: str | None
    ) -> datetime:
        if not MIN_MAINTENANCE_MIN <= duration_min <= MAX_MAINTENANCE_MIN:
            raise ApiError(
                "kiosk.duration_invalid",
                422,
                min=MIN_MAINTENANCE_MIN,
                max=MAX_MAINTENANCE_MIN,
            )
        await self._screen(screen_id)
        until = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=duration_min)
        await upsert_override(
            self._s,
            "screen",
            screen_id,
            CORE_NAMESPACE,
            UNTIL_KEY,
            format_until(until),
            actor.user_id,
        )
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="screen.kiosk.maintenance_started",
            target_type="screen",
            target_id=str(screen_id),
            details={
                "duration_min": duration_min,
                "reason": reason,
                "until": format_until(until),
            },
        )
        await self._s.commit()
        await self._publish(screen_id)
        return until

    async def end_maintenance(self, actor: Principal, screen_id: UUID) -> bool:
        """Fin anticipée. Idempotent : False s'il n'y avait pas de maintenance."""
        await self._screen(screen_id)
        ended = await self._clear(screen_id, None, "user", actor.actor_id, "manual")
        if ended:
            await self._publish(screen_id)
        return ended

    async def expire(self, screen_id: UUID, stored_until: datetime) -> bool:
        """Maintenance échue, détectée à la résolution : retrait + audit système (une fois)."""
        ended = await self._clear(screen_id, stored_until, "system", None, "expired")
        if ended:
            await self._publish(screen_id)
        return ended

    async def _clear(
        self,
        screen_id: UUID,
        only_until: datetime | None,
        actor_kind: str,
        actor_id: str | None,
        why: str,
    ) -> bool:
        cond = [
            ConfigOverride.screen_id == screen_id,
            ConfigOverride.namespace == CORE_NAMESPACE,
            ConfigOverride.key == UNTIL_KEY,
        ]
        if only_until is not None:  # ne retire pas une maintenance relancée entre-temps
            cond.append(ConfigOverride.value == literal(format_until(only_until), JSONB))
        res = await self._s.execute(delete(ConfigOverride).where(*cond))
        if not res.rowcount:  # type: ignore[attr-defined]
            return False
        audit.record(
            self._s,
            actor_kind=actor_kind,
            actor_id=actor_id,
            action="screen.kiosk.maintenance_ended",
            target_type="screen",
            target_id=str(screen_id),
            details={"reason": why},
        )
        await self._s.commit()
        return True
