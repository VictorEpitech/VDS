"""Service du forçage : création, aperçu, prolongation, arrêt ; lecture de la config héritée."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus

from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Organization, Playlist, Screen, ScreenGroup, Site
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion.base import ServiceBase
from vds_server.diffusion.expiry import is_expired
from vds_server.diffusion.playlists import ItemSchemaLookup, PlaylistService
from vds_server.diffusion.resolver import (
    WINDOW_DAYS,
    OverrideSpec,
    Slot,
    resolve_input,
    wall_to_instant,
)
from vds_server.diffusion.schedules import TARGET_MODELS
from vds_server.fleet.health import evaluate
from vds_server.overrides import schemas as sc
from vds_server.player.schedule import _on_chain, load_inputs
from vds_server.power.plan import PowerPlanBuilder
from vds_server.supervision.status import Thresholds

DEFAULT_TZ = "Europe/Paris"
MAX_DEFER_DAYS = 30
DEFAULT_MAX_ACTIVE = 20
MAX_PAGE = 200
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def status_of(row: PlaybackOverride, now: datetime) -> sc.OverrideStatus:
    if row.ended_at is not None or row.ends_at <= now:
        return "ended"
    return "scheduled" if row.starts_at > now else "active"


def to_event(name: str, payload: dict[str, Any]) -> Event:
    return Event(name=name, payload=payload, source="core")


async def screens_of(session: AsyncSession, target_type: str, target_id: UUID) -> list[Screen]:
    """Écrans actifs (non archivés, site non archivé) couverts par une cible."""
    q = (
        select(Screen)
        .join(Site, Site.id == Screen.site_id)
        .where(Screen.archived_at.is_(None), Site.archived_at.is_(None))
        .order_by(Screen.id)
    )
    if target_type == "site":
        q = q.where(Screen.site_id == target_id)
    elif target_type == "group":
        q = q.where(Screen.group_id == target_id)
    elif target_type == "screen":
        q = q.where(Screen.id == target_id)
    return list((await session.execute(q)).scalars())


class OverrideService(ServiceBase):
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        schemas: ConfigSchemaProvider,
        item_schema: ItemSchemaLookup,
        clock: Clock = _utcnow,
    ) -> None:
        super().__init__(session, bus, actor)
        self._schemas = schemas
        self._item_schema = item_schema
        self._clock = clock
        self._cfg = ConfigService(session, schemas)

    # --- cible, config ---------------------------------------------------------------
    async def _target_name(self, target_type: str, target_id: UUID) -> str:
        row = await self._s.get(TARGET_MODELS[target_type], target_id)
        if row is None:
            raise ApiError("override.target_not_found", 422, target_type=target_type)
        return str(row.name)

    async def _config(self, target_type: str, target_id: UUID) -> dict[str, Any]:
        res = await self._cfg.resolve(target_type, target_id, CORE_NAMESPACE)
        return {k: v.value for k, v in res.values.items()}

    async def _respect_of(self, row: PlaybackOverride) -> bool:
        if row.respect_scheduled_off is not None:
            return row.respect_scheduled_off
        cfg = await self._config(row.target_type, row.target_id)
        return bool(cfg.get("override.respect_scheduled_off", False))

    async def _out(self, row: PlaybackOverride, now: datetime) -> sc.OverrideOut:
        ephemeral = False
        if row.playlist_id is not None:
            pl = await self._s.get(Playlist, row.playlist_id)
            ephemeral = pl is not None and pl.owner_override_id is not None
        return sc.OverrideOut(
            id=row.id,
            name=row.name,
            kind=row.kind,  # type: ignore[arg-type]
            playlist_id=row.playlist_id,
            ephemeral=ephemeral,
            target_type=row.target_type,  # type: ignore[arg-type]
            target_id=row.target_id,
            target_name=await self._target_name(row.target_type, row.target_id),
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            ended_at=row.ended_at,
            end_reason=row.end_reason,  # type: ignore[arg-type]
            status=status_of(row, now),
            priority=row.priority,
            respect_scheduled_off=await self._respect_of(row),
            note=row.note,
            created_by=row.created_by,
            created_by_module=row.created_by_module,
            created_at=row.created_at,
        )

    # --- calcul des bornes -----------------------------------------------------------
    def _check_rights(self, kind: str, target_type: str) -> None:
        """Même règle à la création, à la prolongation et à l'arrêt (sur la ligne en base)."""
        if (kind == "off" or target_type == "organization") and self._actor.role != "admin":
            raise ApiError("override.forbidden_kind", 403)

    async def _times(
        self, body: sc.OverrideCreate, now: datetime, target_name: str
    ) -> tuple[datetime, datetime]:
        given = [
            body.ends_at is not None,
            body.duration_minutes is not None,
            body.until is not None,
        ]
        if sum(given) != 1:
            raise ApiError("override.end_required", 422)
        starts = max(body.starts_at, now) if body.starts_at else now
        if starts - now > timedelta(days=MAX_DEFER_DAYS):
            raise ApiError("override.start_too_far", 422, max_days=MAX_DEFER_DAYS)
        if body.ends_at is not None:
            ends = body.ends_at
        elif body.duration_minutes is not None:
            ends = starts + timedelta(minutes=body.duration_minutes)
        else:
            ends = await self._end_of_day(body.target_type, body.target_id, starts)
        ends = ends.astimezone(UTC)
        if ends <= now or ends <= starts:
            raise ApiError("override.end_in_past", 422)
        await self._check_max(body.target_type, body.target_id, starts, ends)
        await self._check_quota(body.target_type, body.target_id, now)
        return starts.astimezone(UTC), ends

    async def _check_max(
        self, target_type: str, target_id: UUID, starts: datetime, ends: datetime
    ) -> None:
        cfg = await self._config(target_type, target_id)
        max_hours = int(cfg.get("override.max_duration_hours", 168))
        if ends - starts > timedelta(hours=max_hours):
            raise ApiError("override.duration_too_long", 422, max_hours=max_hours)

    async def _check_quota(self, target_type: str, target_id: UUID, now: datetime) -> None:
        """Plafond de forçages non terminés (actifs ou programmés) par cible (anti-DoS)."""
        cfg = await self._config(target_type, target_id)
        limit = int(cfg.get("override.max_active_per_target", DEFAULT_MAX_ACTIVE))
        count = await self._s.scalar(
            select(func.count())
            .select_from(PlaybackOverride)
            .where(
                PlaybackOverride.target_type == target_type,
                PlaybackOverride.target_id == target_id,
                PlaybackOverride.ended_at.is_(None),
                PlaybackOverride.ends_at > now,
            )
        )
        if (count or 0) >= limit:
            raise ApiError("override.too_many_active", 409, max_active=limit)

    async def _end_of_day(self, target_type: str, target_id: UUID, starts: datetime) -> datetime:
        """Minuit suivant dans le fuseau de la cible (site ciblé ; organisation : fuseau org)."""
        name = (await self._config(target_type, target_id)).get("timezone") or DEFAULT_TZ
        try:
            tz = ZoneInfo(str(name))
        except (ZoneInfoNotFoundError, ValueError):
            tz = ZoneInfo(DEFAULT_TZ)
        local: date = starts.astimezone(tz).date()
        return wall_to_instant(tz, local + timedelta(days=1), 0)

    # --- création --------------------------------------------------------------------
    async def _validate(self, body: sc.OverrideCreate) -> str:
        self._check_rights(body.kind, body.target_type)
        name = await self._target_name(body.target_type, body.target_id)
        if body.kind == "off":
            if body.playlist_id is not None or body.content is not None:
                raise ApiError("override.content_ambiguous", 422)
        else:
            if (body.playlist_id is None) == (body.content is None):
                raise ApiError("override.content_ambiguous", 422)
            if body.playlist_id is not None:
                pl = await self._s.get(Playlist, body.playlist_id)
                if pl is None or pl.owner_override_id is not None:
                    raise ApiError("override.playlist_not_found", 422)
                if is_expired(pl, self._clock()):
                    raise ApiError("playlist.expired", 422)
        return name

    async def create(self, body: sc.OverrideCreate, *, module: str | None = None) -> sc.OverrideOut:
        if body.request_id is not None:
            existing = await self._s.scalar(
                select(PlaybackOverride).where(PlaybackOverride.request_id == body.request_id)
            )
            if existing is not None:
                return await self._out(existing, self._clock())
        target_name = await self._validate(body)
        now = self._clock()
        starts, ends = await self._times(body, now, target_name)
        playlist_id = body.playlist_id
        name = body.name or f"{target_name} {starts.astimezone(UTC):%H:%M}"
        ephemeral: Playlist | None = None
        if body.content is not None:
            ephemeral = Playlist(
                name=name, created_by=self._actor.user_id, updated_by=self._actor.user_id
            )
            self._s.add(ephemeral)
            await self._s.flush()
            playlist_id = ephemeral.id
        row = PlaybackOverride(
            name=name,
            kind=body.kind,
            playlist_id=playlist_id,
            target_type=body.target_type,
            target_id=body.target_id,
            starts_at=starts,
            ends_at=ends,
            priority=body.priority,
            respect_scheduled_off=body.respect_scheduled_off,
            note=body.note,
            created_by=self._actor.user_id,
            created_by_module=module,
            request_id=body.request_id,
        )
        self._s.add(row)
        await self._s.flush()
        if ephemeral is not None and body.content is not None:
            ephemeral.owner_override_id = row.id
            await PlaylistService(
                self._s, None, self._actor, self._item_schema
            ).add_item_uncommitted(
                ephemeral.id,
                body.content.content_type,
                body.content.props,
                body.content.duration_ms,
            )
        screens = await screens_of(self._s, row.target_type, row.target_id)
        self._audit(
            "override.create",
            "override",
            row.id,
            "override_id",
            emit=False,
            target_type=row.target_type,
            target_id=str(row.target_id),
            ends_at=ends.isoformat(),
            playlist_id=None if playlist_id is None else str(playlist_id),
            screens=len(screens),
        )
        self._events.append(
            to_event(
                "override.created",
                sc.OverrideCreatedV1(
                    override_id=row.id,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    kind=body.kind,
                    playlist_id=playlist_id,
                    starts_at=starts,
                    ends_at=ends,
                    created_by=row.created_by,
                    created_by_module=module,
                ).model_dump(mode="json"),
            )
        )
        try:
            await self._commit("override.conflict")
        except ApiError:
            if body.request_id is not None:
                again = await self._s.scalar(
                    select(PlaybackOverride).where(PlaybackOverride.request_id == body.request_id)
                )
                if again is not None:
                    return await self._out(again, self._clock())
            raise
        await self._s.refresh(row)
        return await self._out(row, now)

    # --- aperçu ----------------------------------------------------------------------
    async def preview(self, body: sc.OverrideCreate) -> sc.OverridePreviewOut:
        target_name = await self._validate(body)
        now = self._clock()
        starts, ends = await self._times(body, now, target_name)
        screens = await screens_of(self._s, body.target_type, body.target_id)
        org_cfg = await self._org_config()
        health = await evaluate(self._s, screens, now, Thresholds.from_config(org_cfg))
        builder = PowerPlanBuilder(self._s, self._schemas)
        off = 0
        for sc_ in screens:
            plan = await builder.for_screen(sc_.id, now, with_overrides=False)
            if any(s.state == "off" and s.start < ends and s.end > starts for s in plan.slots):
                off += 1
        return sc.OverridePreviewOut(
            screens_total=len(screens),
            screens_offline=sum(1 for h in health.values() if h.status == "offline"),
            screens_in_scheduled_off=off,
            ends_at=ends,
            superseded_override_ids=await self._superseded(body, screens, starts, ends, now),
        )

    async def _org_config(self) -> dict[str, Any]:
        org = await self._s.scalar(select(Organization.id))
        return {} if org is None else await self._config("organization", org)

    async def _superseded(
        self,
        body: sc.OverrideCreate,
        screens: list[Screen],
        starts: datetime,
        ends: datetime,
        now: datetime,
    ) -> list[UUID]:
        """Forçages existants que le nouveau masque : ils gagnaient un créneau de [starts, ends)
        avant, plus après (même résolveur que /planning/simulate, fenêtre J et J+1)."""
        cfgs: dict[UUID, dict[str, Any]] = {}

        async def cfg_of(sid: UUID) -> dict[str, Any]:
            if sid not in cfgs:
                cfgs[sid] = await self._config("screen", sid)
            return cfgs[sid]

        async def timezone_of(sid: UUID) -> object:
            return (await cfg_of(sid)).get("timezone") or DEFAULT_TZ

        async def respect_of(sid: UUID) -> object:
            return (await cfg_of(sid)).get("override.respect_scheduled_off", False)

        inputs = await load_inputs(
            self._s, screens, now, timezone_of=timezone_of, respect_of=respect_of
        )
        draft_id = uuid4()
        playlist_id = (
            None
            if body.kind == "off"
            else (body.playlist_id or uuid4())  # contenu : playlist à venir
        )
        out: set[UUID] = set()
        for sid, inp in inputs.items():
            respect = body.respect_scheduled_off
            spec = OverrideSpec(
                id=draft_id, target_type=body.target_type, target_id=body.target_id,
                starts_at=starts, ends_at=ends, playlist_id=playlist_id,
                priority=body.priority, created_at=now,
                respect_scheduled_off=(
                    bool((await cfg_of(sid)).get("override.respect_scheduled_off", False))
                    if respect is None
                    else respect
                ),
            )  # fmt: skip
            if not _on_chain(dict(inp.chain), spec.target_type, spec.target_id):
                continue
            after_inp = replace(inp, overrides=(*inp.overrides, spec))
            for offset in range(WINDOW_DAYS):
                day = inp.day + timedelta(days=offset)
                before = _winners(resolve_input(replace(inp, day=day)), starts, ends)
                after = _winners(resolve_input(replace(after_inp, day=day)), starts, ends)
                out |= before - after
        return sorted(out)

    # --- lecture ---------------------------------------------------------------------
    async def _get(self, override_id: UUID) -> PlaybackOverride:
        row = await self._s.get(PlaybackOverride, override_id)
        if row is None:
            raise ApiError("override.not_found", 404)
        return row

    async def get(self, override_id: UUID) -> sc.OverrideOut:
        return await self._out(await self._get(override_id), self._clock())

    async def list_all(
        self,
        *,
        active: bool | None,
        site_id: UUID | None,
        target_type: str | None,
        target_id: UUID | None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[sc.OverrideOut]:
        now = self._clock()
        q = select(PlaybackOverride).order_by(
            PlaybackOverride.created_at.desc(), PlaybackOverride.id
        )
        if active is True:
            q = q.where(PlaybackOverride.ended_at.is_(None), PlaybackOverride.ends_at > now)
        elif active is False:
            q = q.where(
                or_(PlaybackOverride.ended_at.is_not(None), PlaybackOverride.ends_at <= now)
            )
        if target_type:
            q = q.where(PlaybackOverride.target_type == target_type)
        if target_id:
            q = q.where(PlaybackOverride.target_id == target_id)
        if site_id:
            groups = select(ScreenGroup.id).where(ScreenGroup.site_id == site_id)
            screens = select(Screen.id).where(Screen.site_id == site_id)
            q = q.where(
                or_(
                    PlaybackOverride.target_type == "organization",
                    (PlaybackOverride.target_type == "site")
                    & (PlaybackOverride.target_id == site_id),
                    (PlaybackOverride.target_type == "group")
                    & PlaybackOverride.target_id.in_(groups),
                    (PlaybackOverride.target_type == "screen")
                    & PlaybackOverride.target_id.in_(screens),
                )
            )
        q = q.limit(min(max(limit, 1), MAX_PAGE)).offset(max(offset, 0))
        rows = list((await self._s.execute(q)).scalars())
        return [await self._out(r, now) for r in rows]

    # --- prolongation, arrêt ---------------------------------------------------------
    async def update(self, override_id: UUID, body: sc.OverridePatch) -> sc.OverrideOut:
        row = await self._get(override_id)
        self._check_rights(row.kind, row.target_type)
        now = self._clock()
        if status_of(row, now) == "ended":
            raise ApiError("override.already_ended", 409)
        fields = body.model_fields_set
        if body.ends_at is not None:
            ends = body.ends_at.astimezone(UTC)
            if ends <= now or ends <= row.starts_at:
                raise ApiError("override.end_in_past", 422)
            await self._check_max(row.target_type, row.target_id, row.starts_at, ends)
            row.ends_at = ends
        if "note" in fields:
            row.note = body.note
        row.updated_at = now
        self._audit(
            "override.update",
            "override",
            row.id,
            "override_id",
            emit=False,
            target_type=row.target_type,
            target_id=str(row.target_id),
            fields=sorted(fields),
            ends_at=row.ends_at.isoformat(),
        )
        if body.ends_at is not None:
            self._events.append(
                to_event(
                    "override.updated",
                    sc.OverrideUpdatedV1(
                        override_id=row.id,
                        target_type=row.target_type,
                        target_id=row.target_id,
                        ends_at=row.ends_at,
                    ).model_dump(mode="json"),
                )
            )
        await self._commit("override.conflict")
        return await self._out(row, now)

    async def stop(self, override_id: UUID) -> sc.OverrideOut:
        row = await self._get(override_id)
        self._check_rights(row.kind, row.target_type)
        now = self._clock()
        if status_of(row, now) == "ended":  # idempotent
            return await self._out(row, now)
        row.ended_at, row.end_reason, row.ended_by = now, "stopped", self._actor.user_id
        row.updated_at = now
        self._audit(
            "override.stop",
            "override",
            row.id,
            "override_id",
            emit=False,
            target_type=row.target_type,
            target_id=str(row.target_id),
        )
        self._events.append(
            to_event(
                "override.stopped",
                sc.OverrideStoppedV1(
                    override_id=row.id,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    ended_at=now,
                    ended_by=self._actor.user_id,
                ).model_dump(mode="json"),
            )
        )
        await self._commit("override.conflict")
        return await self._out(row, now)


def _winners(slots: list[Slot], starts: datetime, ends: datetime) -> set[UUID]:
    return {
        s.override_id
        for s in slots
        if s.override_id is not None and s.start < ends and s.end > starts
    }
