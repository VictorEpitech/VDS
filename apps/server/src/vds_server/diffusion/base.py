"""Socle commun des services de diffusion : audit, événements publiés après commit."""

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError


class ServiceBase:
    def __init__(self, session: AsyncSession, bus: EventBus | None, actor: Principal) -> None:
        self._s = session
        self._bus = bus
        self._actor = actor
        self._events: list[Event] = []

    def _audit(
        self,
        action: str,
        target_type: str,
        target_id: UUID,
        payload_key: str,
        /,
        *,
        emit: bool = True,
        **details: Any,
    ) -> None:
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
        if emit:
            self._events.append(
                Event(name=action, payload={payload_key: str(target_id), **details}, source="core")
            )

    async def _commit(self, conflict_code: str) -> None:
        try:
            await self._s.commit()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError(conflict_code, 409) from exc
        events, self._events = self._events, []
        if self._bus is not None:
            for event in events:
                await self._bus.publish(event)
