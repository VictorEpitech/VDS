"""Mises en page : CRUD, usage, duplication, purge (docs/architecture-mises-en-page.md §2, §3, §8).

Une mise en page est une donnée du noyau (pas un module). Le résolveur de diffusion reste unique
(`layout_content.resolve_play_content`) ; ce service ne gère que le stockage et ses garde-fous.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import EventBus

from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Media, Organization, Playlist, Schedule
from vds_server.db.models_layout import Layout, LayoutZone
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion import layout_schemas as ls
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.base import ServiceBase
from vds_server.diffusion.scope import ContentScope, OrganizationScope
from vds_server.diffusion.tags import escape_like, normalize_tag, normalize_tags
from vds_server.diffusion.targets import TARGET_MODELS, screens_of

DEFAULT_MAX_ZONES = 12
SCALE = 10_000
MISMATCH = "layout_orientation_mismatch"


def _units(v: float) -> int:
    return round(v * SCALE)


def zone_out(z: LayoutZone) -> ls.LayoutZoneOut:
    return ls.LayoutZoneOut(
        key=z.key, name=z.name, x=z.x / SCALE, y=z.y / SCALE, w=z.w / SCALE, h=z.h / SCALE,
        z=z.z, playlist_id=z.playlist_id, fit=z.fit,  # type: ignore[arg-type]
        default_item_duration_ms=z.default_item_duration_ms,
        active_from=z.active_from, active_until=z.active_until,
    )  # fmt: skip


async def orientation_mismatch(
    session: AsyncSession, layout_id: UUID, target_type: str, target_id: UUID
) -> int:
    """Nombre d'écrans ciblés dont l'orientation diffère de celle de la mise en page."""
    orientation = await session.scalar(select(Layout.orientation).where(Layout.id == layout_id))
    if orientation is None:
        return 0
    screens = await screens_of(session, target_type, target_id)
    return sum(1 for s in screens if s.orientation != orientation)


async def touch_layouts(session: AsyncSession, layout_ids: list[UUID]) -> list[tuple[UUID, int]]:
    """Incrémente `revision` (et `updated_at`) ; retourne (id, nouvelle révision) triés."""
    if not layout_ids:
        return []
    rows = await session.execute(
        update(Layout)
        .where(Layout.id.in_(layout_ids))
        .values(revision=Layout.revision + 1, updated_at=datetime.now(UTC))
        .returning(Layout.id, Layout.revision)
    )
    return sorted(((i, r) for i, r in rows), key=lambda t: str(t[0]))


class LayoutService(ServiceBase):
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        schemas: ConfigSchemaProvider | None = None,
        scope: ContentScope | None = None,
    ) -> None:
        super().__init__(session, bus, actor)
        self._schemas = schemas
        self._scope: ContentScope = scope or OrganizationScope()

    # --- lecture ---------------------------------------------------------------------
    async def _org_id(self) -> UUID:
        org = await self._s.scalar(select(Organization.id))
        if org is None:  # installation sans organisation : ne peut pas arriver après onboarding
            raise ApiError("layout.organization_missing", 409)
        return UUID(str(org))

    async def _max_zones(self, org_id: UUID) -> int:
        if self._schemas is None:
            return DEFAULT_MAX_ZONES
        res = await ConfigService(self._s, self._schemas).resolve(
            "organization", org_id, CORE_NAMESPACE
        )
        value = res.values.get("layout.max_zones")
        return DEFAULT_MAX_ZONES if value is None else int(value.value)

    async def _row(self, layout_id: UUID, *, lock: bool = False) -> Layout:
        q = select(Layout).where(Layout.id == layout_id, self._scope.layout_filter(self._actor))
        if lock:
            q = q.with_for_update()
        row = (await self._s.execute(q)).scalar_one_or_none()
        if row is None:
            raise ApiError("layout.not_found", 404)
        return row

    async def _zones(self, layout_ids: list[UUID]) -> dict[UUID, list[LayoutZone]]:
        out: dict[UUID, list[LayoutZone]] = {i: [] for i in layout_ids}
        if layout_ids:
            q = (
                select(LayoutZone)
                .where(LayoutZone.layout_id.in_(layout_ids))
                .order_by(LayoutZone.z, LayoutZone.key)
            )
            for z in (await self._s.execute(q)).scalars():
                out[z.layout_id].append(z)
        return out

    def _out(self, row: Layout, zones: list[LayoutZone]) -> ls.LayoutOut:
        return ls.LayoutOut(
            id=row.id,
            name=row.name,
            orientation=row.orientation,  # type: ignore[arg-type]
            background_color=row.background_color,
            background_media_id=row.background_media_id,
            tags=list(row.tags or []),
            zones=[zone_out(z) for z in zones],
            revision=row.revision,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def get(self, layout_id: UUID) -> ls.LayoutOut:
        row = await self._row(layout_id)
        return self._out(row, (await self._zones([row.id]))[row.id])

    async def _usage_counts(self, ids: list[UUID], now: datetime) -> dict[UUID, int]:
        out: dict[UUID, int] = dict.fromkeys(ids, 0)
        if not ids:
            return out
        for lid, n in await self._s.execute(
            select(Schedule.layout_id, func.count())
            .where(Schedule.layout_id.in_(ids))
            .group_by(Schedule.layout_id)
        ):
            if lid is not None:
                out[lid] += n
        for lid, n in await self._s.execute(
            select(PlaybackOverride.layout_id, func.count())
            .where(
                PlaybackOverride.layout_id.in_(ids),
                PlaybackOverride.ended_at.is_(None),
                PlaybackOverride.ends_at > now,
            )
            .group_by(PlaybackOverride.layout_id)
        ):
            if lid is not None:
                out[lid] += n
        return out

    async def list_page(
        self,
        *,
        search: str | None,
        tag: str | None,
        orientation: str | None,
        limit: int,
        offset: int,
    ) -> ls.LayoutPage:
        where: list[ColumnElement[bool]] = [self._scope.layout_filter(self._actor)]
        if search:
            where.append(Layout.name.ilike(f"%{escape_like(search)}%", escape="\\"))
        if tag:
            where.append(Layout.tags.contains([normalize_tag(tag)]))
        if orientation:
            where.append(Layout.orientation == orientation)
        rows = list(
            (
                await self._s.execute(
                    select(Layout)
                    .where(*where)
                    .order_by(Layout.name, Layout.id)
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
        )
        total = await self._s.scalar(select(func.count()).select_from(Layout).where(*where))
        ids = [r.id for r in rows]
        zones = await self._zones(ids)
        usage = await self._usage_counts(ids, datetime.now(UTC))
        return ls.LayoutPage(
            items=[
                ls.LayoutSummary(
                    id=r.id,
                    name=r.name,
                    orientation=r.orientation,  # type: ignore[arg-type]
                    tags=list(r.tags or []),
                    revision=r.revision,
                    zone_count=len(zones[r.id]),
                    usage_count=usage[r.id],
                    zones=[zone_out(z) for z in zones[r.id]],
                    updated_at=r.updated_at,
                )
                for r in rows
            ],
            total=int(total or 0),
        )

    # --- validation ------------------------------------------------------------------
    async def _validate(self, body: ls.LayoutIn, org_id: UUID) -> None:
        max_zones = await self._max_zones(org_id)
        if len(body.zones) > max_zones:
            raise ApiError("layout.too_many_zones", 422, max=max_zones)
        keys = [z.key for z in body.zones]
        if len(set(keys)) != len(keys):
            raise ApiError("layout.zone_key_duplicate", 422)
        for z in body.zones:
            if _units(z.x) + _units(z.w) > SCALE or _units(z.y) + _units(z.h) > SCALE:
                raise ApiError("layout.zone_out_of_bounds", 422, zone=z.key)
            if _units(z.w) < 1 or _units(z.h) < 1:
                raise ApiError("layout.zone_out_of_bounds", 422, zone=z.key)
        playlists = {z.playlist_id for z in body.zones if z.playlist_id is not None}
        if playlists:
            found = set(
                (
                    await self._s.execute(
                        select(Playlist.id).where(
                            Playlist.id.in_(playlists),
                            Playlist.owner_override_id.is_(None),
                            self._scope.playlist_filter(self._actor),
                        )
                    )
                ).scalars()
            )
            if found != playlists:
                raise ApiError("layout.playlist_not_found", 422)
        if body.background_media_id is not None:
            media = await self._s.scalar(
                select(Media).where(
                    Media.id == body.background_media_id, self._scope.media_filter(self._actor)
                )
            )
            if media is None:
                raise ApiError("layout.media_not_found", 422)
            if media.kind != "image":
                raise ApiError("layout.background_not_image", 422)
        normalize_tags(body.tags)

    def _zone_rows(self, layout_id: UUID, body: ls.LayoutIn) -> list[LayoutZone]:
        return [
            LayoutZone(
                layout_id=layout_id,
                key=z.key,
                name=z.name,
                x=_units(z.x),
                y=_units(z.y),
                w=_units(z.w),
                h=_units(z.h),
                z=z.z,
                playlist_id=z.playlist_id,
                fit=z.fit,
                default_item_duration_ms=z.default_item_duration_ms,
                active_from=z.active_from,
                active_until=z.active_until,
            )  # fmt: skip
            for z in body.zones
        ]

    # --- écriture --------------------------------------------------------------------
    async def _flush(self, conflict_code: str) -> None:
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError(conflict_code, 409) from exc

    async def create(self, body: ls.LayoutIn) -> ls.LayoutOut:
        org_id = await self._org_id()
        await self._validate(body, org_id)
        row = Layout(
            org_id=org_id,
            name=body.name,
            orientation=body.orientation,
            background_color=body.background_color,
            background_media_id=body.background_media_id,
            tags=normalize_tags(body.tags),
            created_by=self._actor.user_id,
        )
        self._s.add(row)
        await self._flush("layout.name_conflict")
        self._s.add_all(self._zone_rows(row.id, body))
        self._audit("layout.created", "layout", row.id, "layout_id", zones=len(body.zones))
        await self._commit("layout.name_conflict")
        return await self.get(row.id)

    async def update(self, layout_id: UUID, body: ls.LayoutIn, revision: int) -> ls.LayoutOut:
        org_id = await self._org_id()
        row = await self._row(layout_id, lock=True)
        if not await self._scope.can_write_layout(self._actor, layout_id):
            raise ApiError("layout.forbidden", 403)
        if row.revision != revision:
            raise ApiError("layout.revision_conflict", 412, revision=row.revision)
        await self._validate(body, org_id)
        row.name = body.name
        row.orientation = body.orientation
        row.background_color = body.background_color
        row.background_media_id = body.background_media_id
        row.tags = normalize_tags(body.tags)
        row.revision += 1
        row.updated_at = datetime.now(UTC)
        for old in (await self._zones([row.id]))[row.id]:
            await self._s.delete(old)
        await self._flush("layout.name_conflict")
        self._s.add_all(self._zone_rows(row.id, body))
        self._audit("layout.updated", "layout", row.id, "layout_id", revision=row.revision)
        await self._commit("layout.name_conflict")
        return await self.get(row.id)

    async def duplicate(self, layout_id: UUID) -> ls.LayoutOut:
        src = await self._row(layout_id)
        zones = (await self._zones([src.id]))[src.id]
        existing = set(
            (await self._s.scalars(select(Layout.name).where(Layout.org_id == src.org_id))).all()
        )
        base = f"{src.name[:100]} (copie)"
        name, n = base, 1
        while name in existing:
            n += 1
            name = f"{base} {n}"
        row = Layout(
            org_id=src.org_id,
            name=name,
            orientation=src.orientation,
            background_color=src.background_color,
            background_media_id=src.background_media_id,
            tags=list(src.tags or []),
            created_by=self._actor.user_id,
        )
        self._s.add(row)
        await self._flush("layout.name_conflict")
        self._s.add_all(
            LayoutZone(
                layout_id=row.id,
                key=z.key,
                name=z.name,
                x=z.x,
                y=z.y,
                w=z.w,
                h=z.h,
                z=z.z,
                playlist_id=z.playlist_id,
                fit=z.fit,
                default_item_duration_ms=z.default_item_duration_ms,
                active_from=z.active_from,
                active_until=z.active_until,
            )  # fmt: skip
            for z in zones
        )
        self._audit("layout.created", "layout", row.id, "layout_id", duplicate_of=str(src.id))
        await self._commit("layout.name_conflict")
        return await self.get(row.id)

    async def delete(self, layout_id: UUID) -> None:
        row = await self._row(layout_id, lock=True)
        if not await self._scope.can_write_layout(self._actor, layout_id):
            raise ApiError("layout.forbidden", 403)
        schedules = (
            await self._s.scalars(select(Schedule.id).where(Schedule.layout_id == layout_id))
        ).all()
        overrides = (
            await self._s.scalars(
                select(PlaybackOverride.id).where(PlaybackOverride.layout_id == layout_id)
            )
        ).all()
        if schedules or overrides:
            raise ApiError(
                "layout.in_use",
                409,
                schedules=sorted(str(i) for i in schedules),
                overrides=sorted(str(i) for i in overrides),
            )
        await self._s.delete(row)
        self._audit("layout.deleted", "layout", layout_id, "layout_id")
        await self._commit("layout.in_use")

    # --- usage -----------------------------------------------------------------------
    async def usage(self, layout_id: UUID) -> ls.LayoutUsageOut:
        layout = await self._row(layout_id)
        now = datetime.now(UTC)
        zone_keys = [z.key for z in (await self._zones([layout.id]))[layout.id]]
        sched = list(
            (
                await self._s.execute(
                    select(Schedule)
                    .where(Schedule.layout_id == layout_id)
                    .order_by(Schedule.name, Schedule.id)
                )
            ).scalars()
        )
        names: dict[tuple[str, UUID], str] = {}
        for kind, model in TARGET_MODELS.items():
            ids = {r.target_id for r in sched if r.target_type == kind}
            if ids:
                for tid, name in await self._s.execute(
                    select(model.id, model.name).where(model.id.in_(ids))
                ):
                    names[(kind, tid)] = name
        today = now.date()
        schedules = [
            sc.UsageSchedule(
                id=r.id,
                name=r.name,
                playlist_id=None,
                layout_id=r.layout_id,
                target_type=r.target_type,
                target_id=r.target_id,
                target_name=names.get((r.target_type, r.target_id), ""),
                active=r.enabled and (r.ends_on is None or r.ends_on >= today),
            )  # fmt: skip
            for r in sched
        ]
        live = and_(PlaybackOverride.ended_at.is_(None), PlaybackOverride.ends_at > now)
        screen_ovs = list(
            (
                await self._s.execute(
                    select(PlaybackOverride)
                    .where(PlaybackOverride.layout_id == layout_id)
                    .order_by(PlaybackOverride.ends_at.desc(), PlaybackOverride.id)
                )
            ).scalars()
        )
        zone_ovs = list(
            (
                await self._s.execute(
                    select(PlaybackOverride)
                    .where(
                        live,
                        PlaybackOverride.zone_ids.is_not(None),
                        or_(*(PlaybackOverride.zone_ids.contains([k]) for k in zone_keys)),
                    )
                    .order_by(PlaybackOverride.ends_at.desc(), PlaybackOverride.id)
                )
            ).scalars()
            if zone_keys
            else []
        )

        def usage_ov(o: PlaybackOverride) -> ls.LayoutUsageOverride:
            return ls.LayoutUsageOverride(
                id=o.id, name=o.name, layout_id=o.layout_id, zone_ids=o.zone_ids,
                ends_at=o.ends_at,
                active=o.ended_at is None and o.starts_at <= now < o.ends_at,
            )  # fmt: skip

        targets = {(s.target_type, s.target_id) for s in schedules if s.active}
        targets |= {(o.target_type, o.target_id) for o in screen_ovs if usage_ov(o).active}
        screens = {}
        for ttype, tid in sorted(targets, key=str):
            for sc_ in await screens_of(self._s, ttype, tid):
                screens[sc_.id] = sc_
        return ls.LayoutUsageOut(
            schedules=schedules,
            overrides=[usage_ov(o) for o in screen_ovs],
            zone_overrides=[usage_ov(o) for o in zone_ovs],
            screens_total=len(screens),
            screens_orientation_mismatch=sum(
                1 for s in screens.values() if s.orientation != layout.orientation
            ),
        )


def revision_header(value: str | None) -> int:
    """`If-Match: <revision>` (guillemets et préfixe faible tolérés) ; 428 absent, 422 illisible."""
    if value is None or not value.strip():
        raise ApiError("layout.if_match_required", 428)
    raw = value.strip().removeprefix("W/").strip('"')
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError("layout.if_match_invalid", 422) from exc
