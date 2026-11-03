"""Programmation : playlist + cible + période + règles hebdomadaires."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import EventBus

from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import (
    Playlist,
    Schedule,
    ScheduleRule,
)
from vds_server.db.models_layout import Layout
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.base import ServiceBase
from vds_server.diffusion.expiry import is_expired
from vds_server.diffusion.layouts import MISMATCH, orientation_mismatch
from vds_server.diffusion.priority import priority_level
from vds_server.diffusion.scope import ContentScope, OrganizationScope
from vds_server.diffusion.targets import TARGET_MODELS


@dataclass(frozen=True)
class ScheduleValues:
    """Valeurs d'une programmation après application d'un PATCH validé (rules None = inchangées)."""

    name: str
    playlist_id: UUID | None
    layout_id: UUID | None
    target_type: str
    target_id: UUID
    priority: int
    starts_on: date | None
    ends_on: date | None
    enabled: bool
    rules: list[sc.RuleIn] | None


class ScheduleService(ServiceBase):
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        scope: ContentScope | None = None,
    ) -> None:
        super().__init__(session, bus, actor)
        self._scope: ContentScope = scope or OrganizationScope()

    async def _target_exists(self, target_type: str, target_id: UUID) -> None:
        if await self._s.get(TARGET_MODELS[target_type], target_id) is None:
            raise ApiError("schedule.target_not_found", 422, target_type=target_type)

    async def _playlist_exists(self, playlist_id: UUID) -> None:
        playlist = await self._s.get(Playlist, playlist_id)
        if playlist is None:
            raise ApiError("schedule.playlist_not_found", 422)
        if is_expired(playlist, datetime.now(UTC)):
            raise ApiError("playlist.expired", 422)

    async def _layout_exists(self, layout_id: UUID) -> None:
        # même code qu'une mise en page inconnue : aucune fuite d'existence (422)
        if (
            await self._s.scalar(
                select(Layout.id).where(
                    Layout.id == layout_id, self._scope.layout_filter(self._actor)
                )
            )
            is None
        ):
            raise ApiError("schedule.layout_not_found", 422)

    async def _content_exists(self, playlist_id: UUID | None, layout_id: UUID | None) -> None:
        if (playlist_id is None) == (layout_id is None):
            raise ApiError("schedule.content_target_ambiguous", 422)
        if playlist_id is not None:
            await self._playlist_exists(playlist_id)
        elif layout_id is not None:
            await self._layout_exists(layout_id)

    async def _rules(self, ids: list[UUID]) -> dict[UUID, list[sc.RuleOut]]:
        out: dict[UUID, list[sc.RuleOut]] = {i: [] for i in ids}
        if ids:
            q = (
                select(ScheduleRule)
                .where(ScheduleRule.schedule_id.in_(ids))
                .order_by(ScheduleRule.start_minute, ScheduleRule.end_minute, ScheduleRule.weekdays)
            )
            for r in (await self._s.execute(q)).scalars():
                out[r.schedule_id].append(
                    sc.RuleOut(
                        weekdays=r.weekdays,
                        start=sc.to_hhmm(r.start_minute),
                        end=sc.to_hhmm(r.end_minute),
                    )
                )
        return out

    def _out(self, s: Schedule, rules: list[sc.RuleOut]) -> sc.ScheduleOut:
        return sc.ScheduleOut(
            id=s.id,
            name=s.name,
            playlist_id=s.playlist_id,
            layout_id=s.layout_id,
            target_type=s.target_type,  # type: ignore[arg-type]
            target_id=s.target_id,
            priority=s.priority,
            priority_level=priority_level(s.priority),
            starts_on=s.starts_on,
            ends_on=s.ends_on,
            enabled=s.enabled,
            rules=rules,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )

    async def list_all(
        self, *, target_type: str | None, target_id: UUID | None, playlist_id: UUID | None
    ) -> list[sc.ScheduleOut]:
        q = select(Schedule).order_by(Schedule.name, Schedule.id)
        if target_type:
            q = q.where(Schedule.target_type == target_type)
        if target_id:
            q = q.where(Schedule.target_id == target_id)
        if playlist_id:
            q = q.where(Schedule.playlist_id == playlist_id)
        rows = list((await self._s.execute(q)).scalars())
        rules = await self._rules([r.id for r in rows])
        return [self._out(r, rules[r.id]) for r in rows]

    async def get_row(self, schedule_id: UUID) -> Schedule:
        row = await self._s.get(Schedule, schedule_id)
        if row is None:
            raise ApiError("schedule.not_found", 404)
        return row

    async def get(self, schedule_id: UUID) -> sc.ScheduleOut:
        row = await self.get_row(schedule_id)
        return self._out(row, (await self._rules([row.id]))[row.id])

    def _add_rules(self, schedule_id: UUID, rules: list[sc.RuleIn]) -> None:
        for r in rules:
            self._s.add(
                ScheduleRule(
                    schedule_id=schedule_id,
                    weekdays=r.weekdays,
                    start_minute=sc.to_minutes(r.start),
                    end_minute=sc.to_minutes(r.end),
                )
            )

    async def validate_create(self, body: sc.ScheduleCreate) -> None:
        """Contrôles de service de POST /schedules (le schéma a déjà validé la forme)."""
        await self._content_exists(body.playlist_id, body.layout_id)
        await self._target_exists(body.target_type, body.target_id)

    async def validate_patch(self, row: Schedule, body: sc.SchedulePatch) -> ScheduleValues:
        """Contrôles de service de PATCH /schedules, sans rien modifier ; retourne le résultat."""
        fields = body.model_fields_set
        playlist_id, layout_id = row.playlist_id, row.layout_id
        if body.playlist_id is not None and body.layout_id is not None:
            raise ApiError("schedule.content_target_ambiguous", 422)
        if body.playlist_id is not None:
            if body.playlist_id != row.playlist_id:
                await self._playlist_exists(body.playlist_id)
            playlist_id, layout_id = body.playlist_id, None
        elif body.layout_id is not None:
            if body.layout_id != row.layout_id:
                await self._layout_exists(body.layout_id)
            playlist_id, layout_id = None, body.layout_id
        target_type, target_id = row.target_type, row.target_id
        if body.target_type is not None or body.target_id is not None:
            if body.target_type is None or body.target_id is None:
                raise ApiError("schedule.target_incomplete", 422)
            await self._target_exists(body.target_type, body.target_id)
            target_type, target_id = body.target_type, body.target_id
        starts: date | None = body.starts_on if "starts_on" in fields else row.starts_on
        ends: date | None = body.ends_on if "ends_on" in fields else row.ends_on
        if starts and ends and starts > ends:
            raise ApiError("schedule.period_invalid", 422)
        return ScheduleValues(
            name=row.name if body.name is None else body.name,
            playlist_id=playlist_id,
            layout_id=layout_id,
            target_type=target_type,
            target_id=target_id,
            priority=row.priority if body.priority is None else body.priority,
            starts_on=starts,
            ends_on=ends,
            enabled=row.enabled if body.enabled is None else body.enabled,
            rules=body.rules,
        )

    async def create(self, body: sc.ScheduleCreate) -> sc.ScheduleOut:
        await self.validate_create(body)
        row = Schedule(
            name=body.name,
            playlist_id=body.playlist_id,
            layout_id=body.layout_id,
            target_type=body.target_type,
            target_id=body.target_id,
            priority=body.priority,
            starts_on=body.starts_on,
            ends_on=body.ends_on,
            enabled=body.enabled,
            created_by=self._actor.user_id,
            updated_by=self._actor.user_id,
        )
        self._s.add(row)
        await self._s.flush()
        self._add_rules(row.id, body.rules)
        self._audit(
            "schedule.created",
            "schedule",
            row.id,
            "schedule_id",
            target_type=body.target_type,
            target_id=str(body.target_id),
        )
        await self._commit("schedule.conflict")
        return await self._with_warnings(await self.get(row.id), row)

    async def _with_warnings(self, out: sc.ScheduleOut, row: Schedule) -> sc.ScheduleOut:
        if row.layout_id is not None:
            n = await orientation_mismatch(self._s, row.layout_id, row.target_type, row.target_id)
            if n:
                out.warnings = [MISMATCH]
                out.screens_orientation_mismatch = n
        return out

    async def update(self, schedule_id: UUID, body: sc.SchedulePatch) -> sc.ScheduleOut:
        row = await self.get_row(schedule_id)
        fields = body.model_fields_set
        v = await self.validate_patch(row, body)
        row.playlist_id, row.layout_id = v.playlist_id, v.layout_id
        row.target_type, row.target_id = v.target_type, v.target_id
        row.name, row.priority, row.enabled = v.name, v.priority, v.enabled
        row.starts_on, row.ends_on = v.starts_on, v.ends_on
        if v.rules is not None:
            q = select(ScheduleRule).where(ScheduleRule.schedule_id == row.id)
            for old in (await self._s.execute(q)).scalars():
                await self._s.delete(old)
            await self._s.flush()
            self._add_rules(row.id, v.rules)
        row.updated_at = datetime.now(UTC)
        row.updated_by = self._actor.user_id
        self._audit("schedule.updated", "schedule", row.id, "schedule_id", fields=sorted(fields))
        await self._commit("schedule.conflict")
        return await self._with_warnings(await self.get(row.id), row)

    async def delete(self, schedule_id: UUID) -> None:
        row = await self.get_row(schedule_id)
        await self._s.delete(row)
        self._audit("schedule.deleted", "schedule", schedule_id, "schedule_id")
        await self._commit("schedule.conflict")
