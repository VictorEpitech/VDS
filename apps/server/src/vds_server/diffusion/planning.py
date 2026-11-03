"""Planning admin : timeline, instant, simulation. Aucun résolveur propre.

Tout passe par `resolver.resolve_input` (mode `explain`) et `explain.conflicts/markers` ; ce
module ne fait que charger (nombre de requêtes SQL constant), découper en tranches de 2 jours,
nommer les sources et assembler les sorties. Les écrans n'ont ni N+1 ni boucle SQL.
"""

import base64
import binascii
import hashlib
import json
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.config import resolver as cfg_resolver
from vds_server.config.schemas import CORE_NAMESPACE, ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import (
    ConfigOverride,
    Media,
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    ScheduleRule,
    Screen,
    Site,
)
from vds_server.db.models_layout import Layout
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion import explain as explain_mod
from vds_server.diffusion import planning_schemas as ps
from vds_server.diffusion import resolver
from vds_server.diffusion import schemas as sc
from vds_server.diffusion.priority import priority_level
from vds_server.diffusion.resolver import (
    SPECIFICITY,
    WINDOW_DAYS,
    OverrideSpec,
    Ref,
    ResolveInput,
    ScheduleSpec,
    Slot,
    wall_to_instant,
)
from vds_server.diffusion.schedules import ScheduleService
from vds_server.diffusion.scope import ContentScope, OrganizationScope
from vds_server.diffusion.targets import TARGET_MODELS, screens_of
from vds_server.fleet.health import evaluate
from vds_server.player.schedule import DEFAULT_TZ, _zone, load_inputs
from vds_server.supervision.status import Thresholds

ETAG_VERSION = "planning-1"
NEXT_CHANGE_DAYS = 8  # horizon de recherche du prochain changement (4 tranches)
MAX_DATE_OFFSET_DAYS = 5 * 366  # bornes de `from`, `to` et `at` autour de maintenant
_RESPECT_KEY = "override.respect_scheduled_off"


# --- chargement --------------------------------------------------------------------------
@dataclass(frozen=True)
class Names:
    playlist: Mapping[UUID, str]
    schedule: Mapping[UUID, str]
    override: Mapping[UUID, str]
    target: Mapping[tuple[str, UUID], str]
    media: Mapping[UUID, str]
    layout: Mapping[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Aux:
    names: Names
    empty: frozenset[UUID]  # playlists sans élément jouable
    media_until: Mapping[UUID, tuple[tuple[UUID, datetime], ...]]  # playlist -> (média, fin)


@dataclass(frozen=True)
class Draft:
    """Candidat de simulation : son id est marqué `draft` dans les sorties."""

    id: UUID
    is_new: bool


@dataclass(frozen=True)
class Page:
    screens: list[Screen]
    inputs: dict[UUID, ResolveInput]
    next_cursor: str | None


def _encode_cursor(screen: Screen) -> str:
    raw = json.dumps([screen.name, str(screen.id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, UUID]:
    try:
        name, sid = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return str(name), UUID(str(sid))
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ApiError("planning.cursor_invalid", 422) from exc


def _check_window(start: date, end: date, max_days: int, today: date) -> None:
    for d in (start, end):
        if abs((d - today).days) > MAX_DATE_OFFSET_DAYS:
            raise ApiError("planning.date_out_of_range", 422)
    if end < start:
        raise ApiError("planning.window_invalid", 422)
    if (end - start).days + 1 > max_days:
        raise ApiError("planning.window_too_large", 422, max_days=max_days)


class PlanningService:
    def __init__(
        self,
        session: AsyncSession,
        schemas: ConfigSchemaProvider,
        schedules: ScheduleService | None = None,
        clock: Callable[[], datetime] | None = None,
        *,
        actor: Principal,
        scope: ContentScope | None = None,
    ) -> None:
        self._actor = actor
        self._scope: ContentScope = scope or OrganizationScope()
        self._s = session
        self._schemas = schemas
        self._sched = schedules
        self._clock = clock or (lambda: datetime.now(UTC))

    # --- cibles, écrans, configuration ----------------------------------------------------
    async def _ordered_screens(self, target_type: str, target_id: UUID) -> list[Screen]:
        if await self._s.get(TARGET_MODELS[target_type], target_id) is None:
            raise ApiError("planning.target_not_found", 404, target_type=target_type)
        screens = await screens_of(self._s, target_type, target_id)
        if screens:
            allowed = set(
                await self._s.scalars(
                    select(Screen.id).where(
                        Screen.id.in_([x.id for x in screens]),
                        self._scope.screen_filter(self._actor),
                    )
                )
            )
            screens = [x for x in screens if x.id in allowed]
        return sorted(screens, key=lambda s: (s.name, str(s.id)))

    def _page(
        self, screens: list[Screen], cursor: str | None, limit: int
    ) -> tuple[list[Screen], str | None]:
        if cursor:
            after = _decode_cursor(cursor)
            screens = [s for s in screens if (s.name, s.id) > after]
        page = screens[:limit]
        return page, (_encode_cursor(page[-1]) if len(screens) > limit else None)

    async def _core_values(self, screens: Sequence[Screen]) -> dict[UUID, tuple[str, bool]]:
        """Fuseau et `override.respect_scheduled_off` par écran : 2 requêtes quel que soit N."""
        if not screens:
            return {}
        site_ids = {s.site_id for s in screens}
        site_rows = await self._s.execute(
            select(Site.id, Site.organization_id).where(Site.id.in_(site_ids))
        )
        orgs: dict[UUID, UUID] = {sid: org for sid, org in site_rows}
        schema = self._schemas.get_schema(CORE_NAMESPACE)
        chains: dict[UUID, list[cfg_resolver.ChainNode]] = {}
        for s in screens:
            nodes = [
                cfg_resolver.ChainNode("organization", orgs[s.site_id], ""),
                cfg_resolver.ChainNode("site", s.site_id, ""),
            ]
            if s.group_id is not None:
                nodes.append(cfg_resolver.ChainNode("group", s.group_id, ""))
            nodes.append(cfg_resolver.ChainNode("screen", s.id, ""))
            chains[s.id] = nodes
        if schema is None:
            return {s.id: (DEFAULT_TZ, False) for s in screens}
        scope_ids = {n.id for nodes in chains.values() for n in nodes}
        stored = [
            cfg_resolver.StoredOverride(
                cfg_resolver.LEVELS[r.scope_level], r.scope_id, r.key, r.value
            )
            for r in (
                await self._s.execute(
                    select(ConfigOverride).where(
                        ConfigOverride.namespace == CORE_NAMESPACE,
                        ConfigOverride.key.in_(["timezone", _RESPECT_KEY]),
                        ConfigOverride.scope_id.in_(scope_ids),
                    )
                )
            ).scalars()
        ]
        by_scope: dict[UUID, list[cfg_resolver.StoredOverride]] = {}
        for o in stored:
            by_scope.setdefault(o.scope_id, []).append(o)
        out: dict[UUID, tuple[str, bool]] = {}
        memo: dict[tuple[Any, ...], tuple[str, bool]] = {}
        for s in screens:
            mine = [o for n in chains[s.id] for o in by_scope.get(n.id, ())]
            key = tuple(sorted((o.level, o.key, repr(o.value)) for o in mine))
            if key not in memo:
                res = cfg_resolver.resolve(schema, chains[s.id], mine, os.environ, "screen")
                tz = res.values.get("timezone")
                rs = res.values.get(_RESPECT_KEY)
                memo[key] = (
                    str(tz.value) if tz is not None else DEFAULT_TZ,
                    bool(rs.value) if rs is not None else False,
                )
            out[s.id] = memo[key]
        return out

    async def _inputs(
        self, screens: Sequence[Screen], start: date, now: datetime
    ) -> dict[UUID, ResolveInput]:
        core = await self._core_values(screens)

        async def timezone_of(sid: UUID) -> object:
            return core[sid][0]

        async def respect_of(sid: UUID) -> object:
            return core[sid][1]

        anchor = min(now, datetime.combine(start - timedelta(days=1), time.min, UTC))
        return await load_inputs(
            self._s, screens, anchor, timezone_of=timezone_of, respect_of=respect_of
        )

    async def _load_page(
        self, target_type: str, target_id: UUID, start: date, cursor: str | None, limit: int
    ) -> Page:
        screens = await self._ordered_screens(target_type, target_id)
        page, nxt = self._page(screens, cursor, limit)
        inputs = await self._inputs(page, start, self._clock())
        return Page(page, inputs, nxt)

    # --- noms, contenus vides, expirations --------------------------------------------------
    async def _aux(
        self,
        inputs: Collection[Mapping[UUID, ResolveInput]],
        screens: Sequence[Screen],
        now: datetime,
        extra_schedule_names: Mapping[UUID, str] | None = None,
    ) -> Aux:
        specs = {s.id: s for m in inputs for i in m.values() for s in i.schedules}
        ovs = {o.id: o for m in inputs for i in m.values() for o in i.overrides}
        playlists = {s.playlist_id for s in specs.values() if s.playlist_id is not None}
        playlists |= {o.playlist_id for o in ovs.values() if o.playlist_id is not None}
        layouts = {s.layout_id for s in specs.values() if s.layout_id is not None}
        layouts |= {o.layout_id for o in ovs.values() if o.layout_id is not None}
        targets: dict[str, set[UUID]] = {}
        for sp in specs.values():
            targets.setdefault(sp.target_type, set()).add(sp.target_id)
        for ov in ovs.values():
            targets.setdefault(ov.target_type, set()).add(ov.target_id)
        target_names: dict[tuple[str, UUID], str] = {}
        for ttype, ids in targets.items():
            model = TARGET_MODELS[ttype]
            for tid, name in await self._s.execute(
                select(model.id, model.name).where(model.id.in_(ids))
            ):
                target_names[(ttype, tid)] = name
        for s in screens:
            target_names[("screen", s.id)] = s.name
        names_pl = await self._names(Playlist, playlists, self._scope.playlist_filter(self._actor))
        names_lay = await self._names(Layout, layouts)
        names_sch = await self._names(Schedule, set(specs))
        names_sch.update(extra_schedule_names or {})
        names_ov = await self._names(PlaybackOverride, set(ovs))
        empty, media_until, media_names = await self._contents(playlists, now)
        return Aux(
            Names(names_pl, names_sch, names_ov, target_names, media_names, names_lay),
            frozenset(empty),
            media_until,
        )

    async def _names(
        self, model: Any, ids: set[UUID], scope_filter: ColumnElement[bool] | None = None
    ) -> dict[UUID, str]:
        if not ids:
            return {}
        q = select(model.id, model.name).where(model.id.in_(ids))
        if scope_filter is not None:
            q = q.where(scope_filter)
        rows = await self._s.execute(q)
        return {i: n for i, n in rows}

    async def _contents(
        self, playlists: set[UUID], now: datetime
    ) -> tuple[set[UUID], dict[UUID, tuple[tuple[UUID, datetime], ...]], dict[UUID, str]]:
        """Playlists sans élément jouable (aucun élément actif sans média expiré) et expirations."""
        if not playlists:
            return set(), {}, {}
        rows = (
            await self._s.execute(
                select(
                    PlaylistItem.playlist_id,
                    PlaylistItem.id,
                    Media.id,
                    Media.title,
                    Media.expires_at,
                    Media.expired_at,
                    self._scope.media_filter(self._actor),
                )
                .select_from(PlaylistItem)
                .outerjoin(PlaylistItemMedia, PlaylistItemMedia.item_id == PlaylistItem.id)
                .outerjoin(Media, Media.id == PlaylistItemMedia.media_id)
                .where(PlaylistItem.playlist_id.in_(playlists), PlaylistItem.enabled.is_(True))
            )
        ).all()
        playable: dict[UUID, bool] = {}  # item -> aucun média expiré
        item_playlist: dict[UUID, UUID] = {}
        media_names: dict[UUID, str] = {}
        until: dict[UUID, dict[UUID, datetime]] = {}
        for pid, item_id, media_id, title, expires_at, expired_at, visible in rows:
            item_playlist[item_id] = pid
            playable.setdefault(item_id, True)
            if media_id is None:
                continue
            if visible:
                media_names[media_id] = title
            if expired_at is not None or (expires_at is not None and expires_at <= now):
                playable[item_id] = False
            elif expires_at is not None:
                until.setdefault(pid, {})[media_id] = expires_at
        good = {item_playlist[i] for i, ok in playable.items() if ok}
        return (
            playlists - good,
            {pid: tuple(sorted(m.items(), key=lambda kv: str(kv[0]))) for pid, m in until.items()},
            media_names,
        )

    # --- calcul : tranches de 2 jours --------------------------------------------------------
    @staticmethod
    def slots(inp: ResolveInput, start: date, end: date, *, explain: bool) -> list[Slot]:
        """Créneaux de [minuit local de `start`, minuit local de `end`+1) en tranches J/J+1."""
        tz = inp.tz
        lo = wall_to_instant(tz, start, 0)
        hi = wall_to_instant(tz, end + timedelta(days=1), 0)
        out: list[Slot] = []
        day = start
        while day <= end:
            for sl in resolver.resolve_input(replace(inp, day=day), explain=explain):
                a, b = max(sl.start, lo).astimezone(tz), min(sl.end, hi).astimezone(tz)
                if a >= b:
                    continue
                cur = replace(sl, start=a, end=b)
                last = out[-1] if out else None
                if (
                    last is not None
                    and last.end == a
                    and (last.schedule_id, last.override_id, last.reason, last.losers)
                    == (cur.schedule_id, cur.override_id, cur.reason, cur.losers)
                ):
                    out[-1] = replace(last, end=b)
                else:
                    out.append(cur)
            day += timedelta(days=WINDOW_DAYS)
        return out

    # --- assemblage des sorties ----------------------------------------------------------------
    def _source(
        self,
        names: Names,
        draft: Draft | None,
        screen_id: UUID | None,
        *,
        kind: str,
        id: UUID | None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        priority: int | None = None,
        view: tuple[str, UUID] | None = None,
    ) -> ps.SourceRef:
        if kind == "none" or id is None or target_type is None or target_id is None:
            return ps.SourceRef(kind="none")
        is_draft = draft is not None and id == draft.id
        name = (names.override if kind == "override" else names.schedule).get(id)
        inherited = (
            (target_type, target_id) != ("screen", screen_id)
            if view is None
            else (target_type, target_id) != view
        )
        return ps.SourceRef(
            kind="draft" if is_draft else kind,  # type: ignore[arg-type]
            id=None if is_draft and draft is not None and draft.is_new else id,
            name=name,
            target_type=target_type,  # type: ignore[arg-type]
            target_id=target_id,
            target_name=names.target.get((target_type, target_id)),
            inherited=inherited,
            priority=priority,
            priority_level=None if priority is None else priority_level(priority),
        )

    def _ref_source(
        self,
        names: Names,
        draft: Draft | None,
        screen_id: UUID | None,
        ref: Ref,
        view: tuple[str, UUID] | None = None,
    ) -> ps.SourceRef:
        return self._source(
            names, draft, screen_id, kind=ref.kind, id=ref.id, target_type=ref.target_type,
            target_id=ref.target_id, priority=ref.priority, view=view,
        )  # fmt: skip

    @staticmethod
    def _content(
        names: Names, playlist_id: UUID | None, layout_id: UUID | None = None
    ) -> ps.ContentRef | None:
        if layout_id is not None:
            return ps.ContentRef(kind="layout", id=layout_id, name=names.layout.get(layout_id, ""))
        if playlist_id is None:
            return None
        return ps.ContentRef(
            kind="playlist", id=playlist_id, name=names.playlist.get(playlist_id, "")
        )

    def _segment(
        self, slot: Slot, inp: ResolveInput, screen_id: UUID, names: Names, draft: Draft | None
    ) -> ps.Segment:
        by_id = {s.id: s for s in inp.schedules}
        by_ov = {o.id: o for o in inp.overrides}
        if slot.override_id is not None and slot.override_id in by_ov:
            o = by_ov[slot.override_id]
            source = self._source(
                names, draft, screen_id, kind="override", id=o.id, target_type=o.target_type,
                target_id=o.target_id, priority=o.priority,
            )  # fmt: skip
            state: ps.SegmentState = "overridden"
        elif slot.schedule_id is not None and slot.schedule_id in by_id:
            s = by_id[slot.schedule_id]
            source = self._source(
                names, draft, screen_id, kind="schedule", id=s.id, target_type=s.target_type,
                target_id=s.target_id, priority=s.priority,
            )  # fmt: skip
            state = "play"
        else:
            source = ps.SourceRef(kind="none")
            state = "off"
        return ps.Segment(
            start=slot.start.astimezone(UTC),
            end=slot.end.astimezone(UTC),
            state=state,
            content=self._content(names, slot.playlist_id, slot.layout_id),
            source=source,
            reason=slot.reason,
            losers=[
                ps.Loser(
                    source=self._ref_source(names, draft, screen_id, lo.ref),
                    content=self._content(names, lo.ref.playlist_id, lo.ref.layout_id),
                    reason=lo.reason,
                )
                for lo in slot.losers
            ],
        )

    def _conflict(
        self, c: explain_mod.Conflict, names: Names, draft: Draft | None, screen_id: UUID
    ) -> ps.Conflict:
        return ps.Conflict(
            kind=c.kind,
            severity=c.severity,
            source=self._ref_source(names, draft, screen_id, c.source),
            other=None if c.other is None else self._ref_source(names, draft, screen_id, c.other),
            first_at=c.first_at.astimezone(UTC),
            occurrences=c.occurrences,
        )

    @staticmethod
    def _marker_name(m: explain_mod.Marker, names: Names) -> str | None:
        table = {
            "playlist_expires": names.playlist,
            "media_expires": names.media,
            "override_ends": names.override,
            "schedule_starts": names.schedule,
            "schedule_ends": names.schedule,
        }[m.kind]
        return table.get(m.ref_id)

    def _screen_timeline(
        self,
        screen: Screen,
        inp: ResolveInput,
        start: date,
        end: date,
        aux: Aux,
        draft: Draft | None,
        *,
        explain: bool,
    ) -> tuple[ps.ScreenTimeline, list[Slot]]:
        slots = self.slots(inp, start, end, explain=explain)
        used = {s.playlist_id for s in inp.schedules if s.enabled and s.playlist_id is not None}
        used |= {o.playlist_id for o in inp.overrides if o.playlist_id is not None}
        media_until = [mu for pid in sorted(used, key=str) for mu in aux.media_until.get(pid, ())]
        markers = explain_mod.markers(inp, slots, media_until=media_until)
        conflicts = (
            explain_mod.conflicts(inp, slots, empty_playlists=aux.empty & used) if explain else []
        )
        names = aux.names
        return (
            ps.ScreenTimeline(
                screen_id=screen.id,
                screen_name=screen.name,
                timezone=inp.tz.key,
                segments=[self._segment(sl, inp, screen.id, names, draft) for sl in slots],
                markers=[
                    ps.Marker(
                        at=m.at.astimezone(UTC),
                        kind=m.kind,
                        ref_id=m.ref_id,
                        name=self._marker_name(m, names),
                        impact=m.impact,
                    )
                    for m in markers
                ],
                conflicts=[self._conflict(c, names, draft, screen.id) for c in conflicts],
            ),
            slots,
        )

    def _lanes(
        self,
        target: tuple[str, UUID],
        page: Page,
        slots: Mapping[UUID, list[Slot]],
        start: date,
        end: date,
        aux: Aux,
        draft: Draft | None,
    ) -> list[ps.Lane]:
        """Candidats de la cible et de ses ancêtres, avec leurs occurrences avant arbitrage."""
        if not page.screens:
            return []
        first = page.inputs[page.screens[0].id]
        tz = first.tz
        chain = dict(first.chain)
        level = SPECIFICITY[target[0]]
        own = {(t, i) for t, i in chain.items() if SPECIFICITY[t] <= level}
        specs: dict[UUID, ScheduleSpec] = {}
        ovs: dict[UUID, OverrideSpec] = {}
        for inp in page.inputs.values():
            specs.update({s.id: s for s in inp.schedules if (s.target_type, s.target_id) in own})
            ovs.update({o.id: o for o in inp.overrides if (o.target_type, o.target_id) in own})
        lo, hi = wall_to_instant(tz, start, 0), wall_to_instant(tz, end + timedelta(days=1), 0)
        wins: dict[UUID, list[tuple[datetime, datetime]]] = {}
        for sl_list in slots.values():
            for sl in sl_list:
                for key in (sl.schedule_id, sl.override_id):
                    if key is not None:
                        wins.setdefault(key, []).append((sl.start, sl.end))
        lanes: list[ps.Lane] = []
        for spec in sorted(specs.values(), key=lambda x: (str(x.target_type), str(x.id))):
            occ: list[tuple[datetime, datetime]] = []
            day = start
            while day <= end:
                for a, b in resolver._intervals(spec, tz, day, with_until=True):
                    a, b = max(a, lo), min(b, hi)
                    if a < b:
                        occ.append((a, b))
                day += timedelta(days=WINDOW_DAYS)
            lanes.append(
                self._lane(
                    spec.id,
                    "schedule",
                    spec.target_type,
                    spec.target_id,
                    spec.priority,
                    spec.playlist_id,
                    spec.layout_id,
                    occ,
                    wins.get(spec.id, []),
                    target,
                    aux,
                    draft,
                )  # fmt: skip
            )
        for o in sorted(ovs.values(), key=lambda x: str(x.id)):
            a, b = max(o.starts_at, lo), min(o.ends_at, hi)
            if a < b:
                lanes.append(
                    self._lane(
                        o.id,
                        "override",
                        o.target_type,
                        o.target_id,
                        o.priority,
                        o.playlist_id,
                        o.layout_id,
                        [(a, b)],
                        wins.get(o.id, []),
                        target,
                        aux,
                        draft,
                    )  # fmt: skip
                )
        return lanes

    def _lane(
        self,
        id: UUID,
        kind: str,
        target_type: str,
        target_id: UUID,
        priority: int,
        playlist_id: UUID | None,
        layout_id: UUID | None,
        occurrences: list[tuple[datetime, datetime]],
        wins: list[tuple[datetime, datetime]],
        view: tuple[str, UUID],
        aux: Aux,
        draft: Draft | None,
    ) -> ps.Lane:
        occ_u = _union(occurrences)
        total = _measure(occ_u)
        ratio = _measure(_intersect(occ_u, _union(wins))) / total if total else 0.0
        return ps.Lane(
            source=self._source(
                aux.names,
                draft,
                None,
                kind=kind,
                id=id,
                target_type=target_type,
                target_id=target_id,
                priority=priority,
                view=view,
            ),  # fmt: skip
            content=self._content(aux.names, playlist_id, layout_id),
            occurrences=[(a.astimezone(UTC), b.astimezone(UTC)) for a, b in occ_u],
            effective_ratio=round(min(ratio, 1.0), 4),
        )

    def _timeline(
        self,
        target: tuple[str, UUID],
        page: Page,
        start: date,
        end: date,
        aux: Aux,
        now: datetime,
        draft: Draft | None,
        *,
        explain: bool,
    ) -> ps.TimelineOut:
        rows: list[ps.ScreenTimeline] = []
        all_slots: dict[UUID, list[Slot]] = {}
        for screen in page.screens:
            tl, sl = self._screen_timeline(
                screen, page.inputs[screen.id], start, end, aux, draft, explain=explain
            )
            rows.append(tl)
            all_slots[screen.id] = sl
        zones = [page.inputs[s.id].tz for s in page.screens]
        tz = zones[0] if zones else _zone(DEFAULT_TZ)
        return ps.TimelineOut(
            target_type=target[0],  # type: ignore[arg-type]
            target_id=target[1],
            window=(wall_to_instant(tz, start, 0), wall_to_instant(tz, end + timedelta(days=1), 0)),
            timezone=tz.key,
            mixed_timezones=len({z.key for z in zones}) > 1,
            lanes=self._lanes(target, page, all_slots, start, end, aux, draft),
            screens=rows,
            next_cursor=page.next_cursor,
            computed_at=now,
        )

    # --- GET /timeline -----------------------------------------------------------------------
    async def timeline(
        self,
        *,
        target_type: str,
        target_id: UUID,
        start: date,
        end: date,
        explain: bool | None,
        cursor: str | None,
        limit: int,
        if_none_match: str | None,
    ) -> tuple[str, ps.TimelineOut | None]:
        """(ETag, timeline) ; timeline None si `If-None-Match` correspond (304)."""
        _check_window(start, end, ps.MAX_WINDOW_DAYS, self._clock().date())
        explain_on = target_type == "screen" if explain is None else explain
        now = self._clock()
        page = await self._load_page(target_type, target_id, start, cursor, limit)
        aux = await self._aux([page.inputs], page.screens, now)
        etag = _etag(
            [
                str(self._actor.user_id),
                self._actor.role,
                target_type,
                str(target_id),
                str(start),
                str(end),
                explain_on,
                cursor,
                limit,
            ],
            page,
            aux,
        )
        if if_none_match is not None and _matches(if_none_match, etag):
            return etag, None
        out = self._timeline(
            (target_type, target_id), page, start, end, aux, now, None, explain=explain_on
        )
        return etag, out

    # --- GET /at ------------------------------------------------------------------------------
    async def at(
        self,
        *,
        target_type: str,
        target_id: UUID,
        at: datetime | None,
        cursor: str | None,
        limit: int,
    ) -> ps.AtOut:
        now = self._clock()
        moment = now if at is None else at
        if moment.tzinfo is None:
            raise ApiError("planning.at_timezone_required", 422)
        if abs(moment - now) > timedelta(days=MAX_DATE_OFFSET_DAYS):
            raise ApiError("planning.date_out_of_range", 422)
        moment = moment.astimezone(UTC)
        screens = await self._ordered_screens(target_type, target_id)
        screens, nxt = self._page(screens, cursor, limit)
        inputs = await self._inputs(screens, moment.date() - timedelta(days=1), now)
        aux = await self._aux([inputs], screens, now)
        cfg: dict[str, object] = {}
        if screens:
            org = inputs[screens[0].id].chain["organization"]
            res = await ConfigService(self._s, self._schemas).resolve(
                "organization", org, CORE_NAMESPACE
            )
            cfg = {k: v.value for k, v in res.values.items()}
        health = await evaluate(self._s, screens, now, Thresholds.from_config(cfg))
        rows: list[ps.AtScreen] = []
        for screen in screens:
            inp = inputs[screen.id]
            day = moment.astimezone(inp.tz).date()
            cur = next(
                (
                    sl
                    for sl in resolver.resolve_input(replace(inp, day=day), explain=True)
                    if sl.start <= moment < sl.end
                ),
                None,
            )
            if cur is None:  # ne peut pas arriver : la tranche couvre `moment`
                continue
            horizon = day + timedelta(days=NEXT_CHANGE_DAYS - 1)
            coarse = self.slots(inp, day, horizon, explain=False)
            here = next((sl for sl in coarse if sl.start <= moment < sl.end), None)
            window_end = wall_to_instant(inp.tz, horizon + timedelta(days=1), 0)
            nxt_at = here.end if here is not None and here.end < window_end else None
            h = health.get(screen.id)
            rows.append(
                ps.AtScreen(
                    screen_id=screen.id,
                    screen_name=screen.name,
                    segment=self._segment(cur, inp, screen.id, aux.names, None),
                    next_change_at=None if nxt_at is None else nxt_at.astimezone(UTC),
                    presence=h.status if h is not None else "unknown",
                )
            )
        return ps.AtOut(at=moment, screens=rows, next_cursor=nxt)

    # --- POST /simulate -----------------------------------------------------------------------
    async def simulate(self, body: ps.SimulateIn) -> ps.SimulateOut:
        start, end = body.from_, body.to
        _check_window(start, end, ps.MAX_SIMULATE_DAYS, self._clock().date())
        target = (body.target_type, body.target_id)
        now = self._clock()
        draft, add, remove_id, extra = await self._change(body, now)
        screens = await self._ordered_screens(*target)
        if len(screens) > ps.MAX_SCREENS_PER_PAGE:
            raise ApiError("planning.too_many_screens", 422, max=ps.MAX_SCREENS_PER_PAGE)
        before_inputs = await self._inputs(screens, start, now)
        after_inputs = {sid: _apply(inp, add, remove_id) for sid, inp in before_inputs.items()}
        aux = await self._aux([before_inputs, after_inputs], screens, now, extra)
        # le brouillon peut citer une playlist absente des entrées : son nom et ses contenus
        if add is not None and add.playlist_id is not None:
            if add.playlist_id not in aux.names.playlist:
                aux = await self._aux_with_playlist(aux, add.playlist_id, now)
        elif add is not None and add.layout_id not in aux.names.layout:
            names = {**aux.names.layout, **await self._names(Layout, {add.layout_id})}  # type: ignore[arg-type]
            aux = replace(aux, names=replace(aux.names, layout=names))
        before = self._timeline(
            target, Page(screens, before_inputs, None), start, end, aux, now, draft, explain=True
        )
        after = self._timeline(
            target, Page(screens, after_inputs, None), start, end, aux, now, draft, explain=True
        )
        changes = _changes(before, after)
        keys_b = {_ckey(c): c for t in before.screens for c in t.conflicts}
        keys_a = {_ckey(c): c for t in after.screens for c in t.conflicts}
        return ps.SimulateOut(
            after=after,
            changes=changes,
            new_conflicts=[c for k, c in keys_a.items() if k not in keys_b],
            resolved_conflicts=[c for k, c in keys_b.items() if k not in keys_a],
            screens_touched=len({c.screen_id for c in changes}),
        )

    def _require_sched(self) -> ScheduleService:
        if self._sched is None:  # contrat interne : le routeur fournit toujours le service
            raise ApiError("errors.internal", 500)
        return self._sched

    async def _aux_with_playlist(self, aux: Aux, playlist_id: UUID, now: datetime) -> Aux:
        names = dict(aux.names.playlist)
        names.update(
            await self._names(Playlist, {playlist_id}, self._scope.playlist_filter(self._actor))
        )
        empty, media_until, media_names = await self._contents({playlist_id}, now)
        return Aux(
            replace(aux.names, playlist=names, media={**aux.names.media, **media_names}),
            aux.empty | frozenset(empty),
            {**aux.media_until, **media_until},
        )

    async def _change(
        self, body: ps.SimulateIn, now: datetime
    ) -> tuple[Draft | None, ScheduleSpec | None, UUID | None, dict[UUID, str]]:
        """Valide le brouillon comme POST/PATCH /schedules ; (draft, spec ajoutée, id retiré)."""
        svc = self._require_sched()
        if body.remove is not None:
            await svc.get_row(body.remove)
            return None, None, body.remove, {}
        if body.draft is not None:
            d = body.draft
            await svc.validate_create(d)
            until = await self._scoped_until(d.playlist_id)
            draft = Draft(uuid4(), True)
            spec = ScheduleSpec(
                id=draft.id, playlist_id=d.playlist_id, target_type=d.target_type,
                target_id=d.target_id, priority=d.priority, updated_at=now, starts_on=d.starts_on,
                ends_on=d.ends_on, enabled=d.enabled, rules=_rules(d.rules), until=until,
                layout_id=d.layout_id,
            )  # fmt: skip
            return draft, spec, None, {draft.id: d.name}
        if body.edit is None:  # exclu par SimulateIn._one_change
            raise ApiError("planning.simulate_one_change", 422)
        sid, patch = body.edit
        row = await svc.get_row(sid)
        v = await svc.validate_patch(row, patch)
        until = await self._scoped_until(v.playlist_id)
        rules = v.rules if v.rules is not None else await self._stored_rules(sid)
        spec = ScheduleSpec(
            id=sid, playlist_id=v.playlist_id, target_type=v.target_type, target_id=v.target_id,
            priority=v.priority, updated_at=now, starts_on=v.starts_on, ends_on=v.ends_on,
            enabled=v.enabled, rules=_rules(rules), until=until, layout_id=v.layout_id,
        )  # fmt: skip
        return Draft(sid, False), spec, sid, {sid: v.name}

    async def _scoped_until(self, playlist_id: UUID | None) -> datetime | None:
        """Contrôle de périmètre + expiration de la playlist cible (None pour une mise en page)."""
        if playlist_id is None:
            return None
        await self._check_playlist_scope(playlist_id)
        return await self._until(playlist_id)

    async def _check_playlist_scope(self, playlist_id: UUID) -> None:
        if not await self._scope.can_write_playlist(self._actor, playlist_id):
            raise ApiError("planning.playlist_out_of_scope", 403)

    async def _until(self, playlist_id: UUID) -> datetime | None:
        return await self._s.scalar(select(Playlist.expires_at).where(Playlist.id == playlist_id))

    async def _stored_rules(self, schedule_id: UUID) -> list[sc.RuleIn]:
        rows = await self._s.execute(
            select(ScheduleRule)
            .where(ScheduleRule.schedule_id == schedule_id)
            .order_by(ScheduleRule.start_minute, ScheduleRule.end_minute, ScheduleRule.weekdays)
        )
        return [
            sc.RuleIn(
                weekdays=r.weekdays, start=sc.to_hhmm(r.start_minute), end=sc.to_hhmm(r.end_minute)
            )
            for r in rows.scalars()
        ]


# --- fonctions pures ------------------------------------------------------------------------
def _rules(rules: Sequence[sc.RuleIn]) -> tuple[resolver.Rule, ...]:
    return tuple(
        resolver.Rule(r.weekdays, sc.to_minutes(r.start), sc.to_minutes(r.end)) for r in rules
    )


def _apply(inp: ResolveInput, add: ScheduleSpec | None, remove_id: UUID | None) -> ResolveInput:
    """Entrées après le changement : retire `remove_id`, ajoute `add` s'il touche la chaîne."""
    specs = [s for s in inp.schedules if s.id != remove_id]
    if add is not None and inp.chain.get(add.target_type) == add.target_id:
        specs.append(add)
    return replace(inp, schedules=tuple(specs))


def _union(iv: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _intersect(
    x: Sequence[tuple[datetime, datetime]], y: Sequence[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for a, b in x:
        for c, d in y:
            lo, hi = max(a, c), min(b, d)
            if lo < hi:
                out.append((lo, hi))
    return out


def _measure(iv: Sequence[tuple[datetime, datetime]]) -> float:
    return sum((b - a).total_seconds() for a, b in iv)


def _ckey(c: ps.Conflict) -> tuple[str, UUID | None, UUID | None]:
    return (c.kind, c.source.id, None if c.other is None else c.other.id)


def _identity(seg: ps.Segment) -> tuple[str, UUID | None]:
    return (seg.state, None if seg.content is None else seg.content.id)


def _changes(before: ps.TimelineOut, after: ps.TimelineOut) -> list[ps.ChangeOut]:
    """Différences de contenu joué par écran, fusionnées par paire de segments avant/après."""
    out: list[ps.ChangeOut] = []
    after_by = {t.screen_id: t for t in after.screens}
    for tb in before.screens:
        ta = after_by[tb.screen_id]
        points = sorted({p for s in (*tb.segments, *ta.segments) for p in (s.start, s.end)})
        pairs: list[tuple[datetime, datetime, ps.Segment, ps.Segment]] = []
        for a, b in zip(points, points[1:], strict=False):
            sb = next((s for s in tb.segments if s.start <= a and s.end >= b), None)
            sa = next((s for s in ta.segments if s.start <= a and s.end >= b), None)
            if sb is None or sa is None:
                continue
            if pairs and pairs[-1][2] is sb and pairs[-1][3] is sa and pairs[-1][1] == a:
                pairs[-1] = (pairs[-1][0], b, sb, sa)
            else:
                pairs.append((a, b, sb, sa))
        for a, b, sb, sa in pairs:
            if _identity(sb) != _identity(sa):
                out.append(
                    ps.ChangeOut(screen_id=tb.screen_id, start=a, end=b, before=sb, after=sa)
                )
    return out


def _etag(params: list[Any], page: Page, aux: Aux) -> str:
    """Hachage des entrées (programmations, forçages, fuseaux, playlists, expirations)."""
    h = hashlib.sha256()
    h.update(ETAG_VERSION.encode())
    h.update(json.dumps(params, default=str).encode())
    for s in page.screens:
        inp = page.inputs[s.id]
        h.update(repr((s.id, s.name, inp.tz.key, sorted(inp.chain.items()))).encode())
        for x in sorted(inp.schedules, key=lambda y: str(y.id)):
            h.update(repr(x).encode())
        for o in sorted(inp.overrides, key=lambda y: str(y.id)):
            h.update(repr(o).encode())
    n = aux.names
    for table in (n.playlist, n.schedule, n.override, n.media):
        h.update(repr(sorted(table.items(), key=lambda kv: str(kv[0]))).encode())
    h.update(repr(sorted(n.target.items(), key=lambda kv: str(kv[0]))).encode())
    h.update(repr(sorted(aux.empty, key=str)).encode())
    h.update(repr(sorted(aux.media_until.items(), key=lambda kv: str(kv[0]))).encode())
    return f'"{h.hexdigest()}"'


def _matches(header: str, etag: str) -> bool:
    wanted = {part.strip().removeprefix("W/") for part in header.split(",")}
    return "*" in wanted or etag in wanted
