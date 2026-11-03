"""Programmation résolue servie au player (format 1) : chargement, assemblage, révision."""

import hashlib
import json
from collections.abc import Callable, Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import ContractError, Module
from vds_sdk.server.content import ContentType
from vds_sdk.server.media import MediaInfo, MediaKind

from vds_server.core.errors import ApiError
from vds_server.db.models import (
    Media,
    MediaVariant,
    Playlist,
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    ScheduleRule,
    Screen,
    Site,
)
from vds_server.db.models_layout import Layout, LayoutZone
from vds_server.db.models_override import PlaybackOverride
from vds_server.diffusion import resolver
from vds_server.diffusion.expiry import is_expired
from vds_server.diffusion.layout_compose import (
    EMPTY_PLAYLIST_KEY,
    ActiveZoneOverride,
    PlaylistVariant,
    ResolvedDefaults,
    ResolvedLayout,
    iso,
    project_items,
)
from vds_server.diffusion.layout_content import load_layouts, resolve_play_content
from vds_server.diffusion.resolver import OverrideSpec, ResolveInput, ScheduleSpec, Slot

SCHEDULE_FORMAT = 1
DEFAULT_TZ = "Europe/Paris"
SchemaLookup = Callable[[str], dict[str, Any] | None]
InstanceLookup = Callable[[str], Module | None]
PowerLookup = Callable[[UUID, datetime], Any]  # async: -> [{from, to, state}]
TimezoneLookup = Callable[[UUID], Any]  # async: screen_id -> fuseau IANA
KioskLookup = Callable[[Screen, datetime], Any]  # async: -> {"mode", "until"}
DefaultsLookup = Callable[[UUID], Any]  # async: screen_id -> ResolvedDefaults


async def screen_chain(session: AsyncSession, screen: Screen) -> dict[str, UUID]:
    site = await session.get(Site, screen.site_id)
    if site is None:  # FK : ne peut pas arriver
        raise ApiError("errors.screen_not_found", 404)
    chain = {"organization": site.organization_id, "site": site.id, "screen": screen.id}
    if screen.group_id is not None:
        chain["group"] = screen.group_id
    return chain


def _chain_conds(chains: Sequence[dict[str, UUID]], model: Any) -> list[Any]:
    pairs = sorted({(lvl, tid) for c in chains for lvl, tid in c.items()}, key=str)
    return [(model.target_type == lvl) & (model.target_id == tid) for lvl, tid in pairs]


async def _fetch_specs(
    session: AsyncSession, chains: Sequence[dict[str, UUID]]
) -> list[ScheduleSpec]:
    """Schedules activés ciblant l'une des chaînes, règles incluses : 2 requêtes quel que soit N."""
    if not chains:
        return []
    found_rows = (
        await session.execute(
            select(Schedule, Playlist.expires_at)
            .outerjoin(Playlist, Playlist.id == Schedule.playlist_id)
            .where(Schedule.enabled.is_(True), or_(*_chain_conds(chains, Schedule)))
        )
    ).all()
    rows = [r for r, _ in found_rows]
    until = {r.id: exp for r, exp in found_rows}
    rules: dict[UUID, list[resolver.Rule]] = {r.id: [] for r in rows}
    if rows:
        found = await session.execute(
            select(ScheduleRule)
            .where(ScheduleRule.schedule_id.in_(list(rules)))
            .order_by(ScheduleRule.start_minute, ScheduleRule.end_minute, ScheduleRule.weekdays)
        )
        for r in found.scalars():
            rules[r.schedule_id].append(resolver.Rule(r.weekdays, r.start_minute, r.end_minute))
    return [
        ScheduleSpec(
            id=r.id,
            playlist_id=r.playlist_id,
            target_type=r.target_type,
            target_id=r.target_id,
            priority=r.priority,
            updated_at=r.updated_at,
            starts_on=r.starts_on,
            ends_on=r.ends_on,
            enabled=r.enabled,
            rules=tuple(rules[r.id]),
            until=until[r.id],
            layout_id=r.layout_id,
        )
        for r in rows
    ]


def _on_chain(chain: dict[str, UUID], target_type: str, target_id: UUID) -> bool:
    return chain.get(target_type) == target_id


async def load_specs(session: AsyncSession, chain: dict[str, UUID]) -> list[ScheduleSpec]:
    """Schedules activés dont la cible est dans la chaîne de l'écran, règles incluses."""
    return [
        sp
        for sp in await _fetch_specs(session, [chain])
        if _on_chain(chain, sp.target_type, sp.target_id)
    ]


OVERRIDE_LOOKBACK = timedelta(days=2)


def override_spec(
    row: PlaybackOverride, respect: bool, playlist_expires_at: datetime | None = None
) -> OverrideSpec | None:
    """Spec résolveur ; `ends_at` tronqué à l'arrêt manuel et à l'expiration de la playlist.

    None si le forçage n'a jamais joué."""
    ends = row.ends_at if row.ended_at is None else min(row.ends_at, row.ended_at)
    if playlist_expires_at is not None:
        ends = min(ends, playlist_expires_at)
    if ends <= row.starts_at:
        return None
    return OverrideSpec(
        id=row.id,
        target_type=row.target_type,
        target_id=row.target_id,
        starts_at=row.starts_at,
        ends_at=ends,
        playlist_id=row.playlist_id,
        priority=row.priority,
        created_at=row.created_at,
        respect_scheduled_off=respect,
        layout_id=row.layout_id,
    )


def zone_override_spec(
    row: PlaybackOverride, playlist_expires_at: datetime | None = None
) -> ActiveZoneOverride | None:
    """Forçage de zone actif (fin tronquée à l'arrêt manuel et à l'expiration de la playlist)."""
    if row.zone_ids is None or row.playlist_id is None:
        return None
    ends = row.ends_at if row.ended_at is None else min(row.ends_at, row.ended_at)
    if playlist_expires_at is not None:
        ends = min(ends, playlist_expires_at)
    if ends <= row.starts_at:
        return None
    return ActiveZoneOverride(
        override_id=row.id,
        zone_ids=frozenset(row.zone_ids),
        playlist_id=row.playlist_id,
        starts_at=row.starts_at,
        ends_at=ends,
        priority=(row.priority, row.created_at.timestamp(), str(row.id)),
    )


async def _fetch_override_rows(
    session: AsyncSession,
    chains: Sequence[dict[str, UUID]],
    now: datetime,
    *,
    zones: bool = False,
) -> list[tuple[PlaybackOverride, datetime | None]]:
    """Forçages d'écran (`zones=False`, découpent les créneaux) ou de zone (`zones=True`)."""
    if not chains:
        return []
    rows = (
        await session.execute(
            select(PlaybackOverride, Playlist.expires_at)
            .outerjoin(Playlist, Playlist.id == PlaybackOverride.playlist_id)
            .where(
                PlaybackOverride.ends_at > now - OVERRIDE_LOOKBACK,
                PlaybackOverride.zone_ids.is_not(None)
                if zones
                else PlaybackOverride.zone_ids.is_(None),
                or_(*_chain_conds(chains, PlaybackOverride)),
            )
        )
    ).all()
    return [(r, e) for r, e in rows]


def _override_specs(
    rows: Sequence[tuple[PlaybackOverride, datetime | None]],
    chain: dict[str, UUID],
    respect_default: bool,
) -> list[OverrideSpec]:
    specs = (
        override_spec(
            r,
            respect_default if r.respect_scheduled_off is None else r.respect_scheduled_off,
            expires_at,
        )
        for r, expires_at in rows
        if _on_chain(chain, r.target_type, r.target_id)
    )
    return [s for s in specs if s is not None]


async def load_overrides(
    session: AsyncSession,
    chain: dict[str, UUID],
    now: datetime,
    respect_default: bool = False,
) -> list[OverrideSpec]:
    """Forçages de la chaîne de l'écran pouvant toucher la fenêtre J/J+1 (fin tronquée si arrêté).

    `respect_default` : valeur de config résolue pour l'écran quand le forçage ne la fixe pas."""
    return _override_specs(
        await _fetch_override_rows(session, [chain], now), chain, respect_default
    )


async def select_zone_overrides(
    session: AsyncSession,
    *,
    screen_id: UUID,
    window_start: datetime,
    window_end: datetime,
) -> list[ActiveZoneOverride]:
    """Forçages de zone de la chaîne de l'écran touchant la fenêtre (même ciblage que les
    forçages d'écran, filtré `zone_ids IS NOT NULL`)."""
    screen = await session.get(Screen, screen_id)
    if screen is None:
        return []
    chain = await screen_chain(session, screen)
    rows = await _fetch_override_rows(session, [chain], window_start, zones=True)
    out: list[ActiveZoneOverride] = []
    for row, expires_at in rows:
        if not _on_chain(chain, row.target_type, row.target_id):
            continue
        spec = zone_override_spec(row, expires_at)
        if (
            spec is not None
            and spec.starts_at < window_end
            and (spec.ends_at is None or spec.ends_at > window_start)
        ):
            out.append(spec)
    return sorted(out, key=lambda o: str(o.override_id))


async def load_inputs(
    session: AsyncSession,
    screens: Sequence[Screen],
    now: datetime,
    *,
    timezone_of: TimezoneLookup,
    respect_of: TimezoneLookup | None = None,
) -> dict[UUID, ResolveInput]:
    """Entrées du résolveur pour plusieurs écrans : requêtes SQL en nombre constant.

    Seuls le fuseau et le défaut `respect_scheduled_off` (configuration) sont lus par écran."""
    if not screens:
        return {}
    site_ids = {sc.site_id for sc in screens}
    orgs = dict(
        (await session.execute(select(Site.id, Site.organization_id).where(Site.id.in_(site_ids))))
        .tuples()
        .all()
    )
    chains: dict[UUID, dict[str, UUID]] = {}
    for sc in screens:
        if sc.site_id not in orgs:  # FK : ne peut pas arriver
            raise ApiError("errors.screen_not_found", 404)
        chain = {"organization": orgs[sc.site_id], "site": sc.site_id, "screen": sc.id}
        if sc.group_id is not None:
            chain["group"] = sc.group_id
        chains[sc.id] = chain
    all_chains = list(chains.values())
    specs = await _fetch_specs(session, all_chains)
    rows = await _fetch_override_rows(session, all_chains, now)
    out: dict[UUID, ResolveInput] = {}
    for sc in screens:
        chain = chains[sc.id]
        tz = _zone(await timezone_of(sc.id))
        respect = bool(await respect_of(sc.id)) if respect_of is not None else False
        out[sc.id] = ResolveInput(
            chain=chain,
            schedules=tuple(s for s in specs if _on_chain(chain, s.target_type, s.target_id)),
            overrides=tuple(_override_specs(rows, chain, respect)),
            tz=tz,
            day=now.astimezone(tz).date(),
        )
    return out


async def screen_media_ids(
    session: AsyncSession, screen_id: UUID, *, include_expired: bool = False
) -> set[UUID]:
    """Médias référencés par les programmations actives de l'écran (autorisation de lecture).

    Médias et playlists expirés exclus (sauf `include_expired`, pour distinguer 404 et 403)."""
    screen = await session.get(Screen, screen_id)
    if screen is None or screen.archived_at is not None:
        return set()
    specs = await load_specs(session, await screen_chain(session, screen))
    now = datetime.now(UTC)
    horizon = now.date() - timedelta(days=1)
    playlists = {
        s.playlist_id
        for s in specs
        if s.playlist_id is not None
        and (s.ends_on is None or s.ends_on >= horizon)
        and (include_expired or s.until is None or s.until > now)
    }
    chain = await screen_chain(session, screen)
    layout_ids = {
        s.layout_id
        for s in specs
        if s.layout_id is not None and (s.ends_on is None or s.ends_on >= horizon)
    }
    overrides = [o for o in await load_overrides(session, chain, now) if o.ends_at > now]
    playlists |= {o.playlist_id for o in overrides if o.playlist_id is not None}
    layout_ids |= {o.layout_id for o in overrides if o.layout_id is not None}
    zone_ovs = await select_zone_overrides(
        session, screen_id=screen_id, window_start=now, window_end=now + timedelta(days=3)
    )
    playlists |= {z.playlist_id for z in zone_ovs}
    media: set[UUID] = set()
    if layout_ids:
        zone_playlists = await session.execute(
            select(LayoutZone.playlist_id).where(
                LayoutZone.layout_id.in_(layout_ids), LayoutZone.playlist_id.is_not(None)
            )
        )
        playlists |= {p for p in zone_playlists.scalars() if p is not None}
        backgrounds = await session.execute(
            select(Layout.background_media_id).where(
                Layout.id.in_(layout_ids), Layout.background_media_id.is_not(None)
            )
        )
        media |= {m for m in backgrounds.scalars() if m is not None}
    if not playlists:
        return media
    q = (
        select(PlaylistItemMedia.media_id)
        .join(PlaylistItem, PlaylistItem.id == PlaylistItemMedia.item_id)
        .where(PlaylistItem.playlist_id.in_(playlists), PlaylistItem.enabled.is_(True))
    )
    if not include_expired:
        q = q.join(Media, Media.id == PlaylistItemMedia.media_id).where(
            Media.expired_at.is_(None), or_(Media.expires_at.is_(None), Media.expires_at > now)
        )
    return media | set((await session.execute(q)).scalars())


def revision_of(body: dict[str, Any]) -> str:
    """sha256 du JSON canonique hors `server_time` et `revision`."""
    stable = {k: v for k, v in body.items() if k not in ("server_time", "revision")}
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


class ScheduleBuilder:
    def __init__(
        self,
        session: AsyncSession,
        item_schema: SchemaLookup,
        content_entry: SchemaLookup,
        timezone_of: TimezoneLookup,
        instance_of: InstanceLookup | None = None,
        power_of: PowerLookup | None = None,
        kiosk_of: KioskLookup | None = None,
        respect_of: TimezoneLookup | None = None,
        defaults_of: DefaultsLookup | None = None,
    ) -> None:
        self._defaults_of = defaults_of
        self._respect_of = respect_of
        self._kiosk_of = kiosk_of
        self._power_of = power_of
        self._instance_of = instance_of
        self._s = session
        self._item_schema = item_schema
        self._entry = content_entry
        self._tz_of = timezone_of

    async def build(self, screen_id: UUID, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        screen = await self._s.get(Screen, screen_id)
        if screen is None or screen.archived_at is not None:
            raise ApiError("errors.screen_not_found", 404)
        inp = (
            await load_inputs(
                self._s, [screen], now, timezone_of=self._tz_of, respect_of=self._respect_of
            )
        )[screen_id]
        tz = inp.tz
        slots = resolver.resolve_input(inp)
        defaults = (
            ResolvedDefaults() if self._defaults_of is None else await self._defaults_of(screen_id)
        )
        zone_ovs = await select_zone_overrides(
            self._s, screen_id=screen_id, window_start=now, window_end=slots[-1].end
        )
        org_id = inp.chain["organization"]
        layouts = await load_layouts(
            self._s, {s.layout_id for s in slots if s.layout_id is not None}, org_id=org_id
        )
        composed: dict[int, ResolvedLayout] = {}
        for i, sl in enumerate(slots):
            if sl.is_off:
                continue
            composed[i] = await resolve_play_content(
                self._s,
                org_id=org_id,
                playlist_id=sl.playlist_id,
                layout_id=sl.layout_id,
                slot_start=sl.start.astimezone(UTC),
                slot_end=sl.end.astimezone(UTC),
                defaults=defaults,
                zone_overrides=zone_ovs,
                layouts=layouts,
            )
        playlists, assets, types = await self._assemble(list(composed.values()), now)
        body: dict[str, Any] = {
            "format": SCHEDULE_FORMAT,
            "server_time": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "screen": {
                "id": str(screen.id),
                "name": screen.name,
                "orientation": screen.orientation,
            },
            "timezone": tz.key,
            "valid_from": slots[0].start.isoformat(),
            "valid_until": slots[-1].end.isoformat(),
            "slots": [_slot(s, composed.get(i)) for i, s in enumerate(slots)],
            "playlists": playlists,
            "assets": assets,
            "content_types": types,
        }
        if defaults.max_exclusive_elements != 1:
            body["player"] = {"max_exclusive_elements": defaults.max_exclusive_elements}
        if self._power_of is not None:
            body["power"] = await self._power_of(screen_id, now)
        if self._kiosk_of is not None:
            body["kiosk"] = await self._kiosk_of(screen, now)
        body["revision"] = revision_of(body)
        return body

    async def _assemble(
        self, composed: Sequence[ResolvedLayout], now: datetime
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Playlists (variantes de zone), assets (éléments + fonds) et types du document."""
        variants = {v for r in composed for v in r.variants}
        base, assets, types = await self._playlists({v.playlist_id for v in variants}, now)
        playlists = _variants(base, variants)
        if any(z["playlist"] == EMPTY_PLAYLIST_KEY for r in composed for z in r.layout["zones"]):
            playlists[EMPTY_PLAYLIST_KEY] = {"items": []}
        for r in composed:
            if r.background_media_id is None:
                continue
            bg = r.layout.setdefault("background", {})
            asset = await self._asset(r.background_media_id)
            if asset is not None:
                key = f"media/{r.background_media_id}/original"
                assets[key] = asset
                bg["asset"] = key
            if not bg:
                r.layout.pop("background")
        return playlists, assets, types

    async def preview_layout(self, layout_id: UUID, now: datetime | None = None) -> dict[str, Any]:
        """Document à un seul créneau d'1 h pour l'aperçu admin (écran fictif, lecture seule)."""
        now = now or datetime.now(UTC)
        layouts = await load_layouts(self._s, [layout_id])
        spec = layouts.get(layout_id)
        if spec is None:
            raise ApiError("layout.not_found", 404)
        end = now + timedelta(hours=1)
        defaults = ResolvedDefaults()
        composed = await resolve_play_content(
            self._s,
            org_id=UUID(int=0),  # inutilisé : mise en page déjà chargée
            playlist_id=None,
            layout_id=layout_id,
            slot_start=now,
            slot_end=end,
            defaults=defaults,
            layouts=layouts,
        )
        playlists, assets, types = await self._assemble([composed], now)
        body: dict[str, Any] = {
            "format": SCHEDULE_FORMAT,
            "server_time": iso(now),
            "screen": {"id": str(UUID(int=0)), "name": "preview", "orientation": spec.orientation},
            "timezone": "UTC",
            "valid_from": iso(now),
            "valid_until": iso(end),
            "slots": [
                {
                    "start": iso(now),
                    "end": iso(end),
                    "kind": "play",
                    "layout": composed.layout,
                    "source": {
                        "schedule_id": None,
                        "kind": "schedule",
                        "override_id": None,
                        "layout_id": str(layout_id),
                    },
                }
            ],
            "playlists": playlists,
            "assets": assets,
            "content_types": types,
        }
        body["revision"] = revision_of(body)
        return body

    async def _playlists(
        self, ids: set[UUID], now: datetime
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        playlists: dict[str, Any] = {}
        assets: dict[str, Any] = {}
        types: dict[str, Any] = {}
        for pid in sorted(ids):
            items_q = (
                select(PlaylistItem)
                .where(PlaylistItem.playlist_id == pid, PlaylistItem.enabled.is_(True))
                .order_by(PlaylistItem.position)
            )
            out: list[dict[str, Any]] = []
            for item in (await self._s.execute(items_q)).scalars():
                built = await self._item(item, now)
                if built is None:
                    continue
                entry, item_assets = built
                out.append(entry)
                assets.update(item_assets)
                ctype = item.content_type
                if ctype not in types and (info := self._entry(ctype)):
                    types[ctype] = {k: info[k] for k in ("version", "element", "script")}
            playlists[_pl(pid)] = {"items": out}
        return playlists, assets, types

    async def _item(
        self, item: PlaylistItem, now: datetime
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        schema = self._item_schema(item.content_type)
        if schema is None:  # content-type absent ou inactif : élément ignoré
            return None
        props = dict(item.props or {})
        refs: list[str] = []
        found: dict[str, Any] = {}
        until: datetime | None = None
        for name, spec in (schema.get("properties") or {}).items():
            if isinstance(spec, dict) and spec.get("x-vds-media") and props.get(name) is not None:
                media = await self._s.get(Media, UUID(str(props[name])))
                if media is None:
                    continue
                if is_expired(media, now):  # expiré : l'élément n'est plus diffusé
                    return None
                if media.expires_at is not None:
                    until = media.expires_at if until is None else min(until, media.expires_at)
        for name, spec in (schema.get("properties") or {}).items():
            if not (isinstance(spec, dict) and spec.get("x-vds-media")) or props.get(name) is None:
                continue
            media_id = UUID(str(props[name]))
            asset = await self._asset(media_id)
            if asset is None:
                return None
            key = f"media/{media_id}/original"
            props[name] = key
            refs.append(key)
            found[key] = asset
        info = self._entry(item.content_type) or {}
        duration = item.duration_ms
        instance = self._instance_of(item.content_type) if self._instance_of else None
        if isinstance(instance, ContentType):
            resolved = await self._to_player(instance, dict(item.props or {}), schema)
            if resolved is None:
                return None
            props, refs, found, spec_ms = resolved
            duration = duration or spec_ms
        entry = {
            "id": f"it_{item.id}",
            "content_type": item.content_type,
            "duration_ms": duration,
            "props": props,
            "assets": refs,
            "requires_network": bool(info.get("requires_network", False)),
        }
        if until is not None:
            entry["until"] = until.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return entry, found

    async def _to_player(
        self, instance: ContentType, props: dict[str, Any], schema: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str], dict[str, Any], int | None] | None:
        """Résolution par le module (pages PDF…) ; None si un média manque ou n'est pas prêt."""
        infos: dict[UUID, MediaInfo] = {}
        for name, spec in (schema.get("properties") or {}).items():
            if isinstance(spec, dict) and spec.get("x-vds-media") and props.get(name) is not None:
                info = await self._media_info(UUID(str(props[name])))
                if info is None:
                    return None
                infos[info.id] = info
        try:
            spec_out = await instance.to_player(props, infos)
        except ContractError:
            return None
        found: dict[str, Any] = {}
        for key in spec_out.assets:
            _, mid, variant = key.split("/", 2)
            asset = await self._asset(UUID(mid), variant)
            if asset is None:
                return None
            found[key] = asset
        return spec_out.props, list(spec_out.assets), found, spec_out.duration_ms

    async def _media_info(self, media_id: UUID) -> MediaInfo | None:
        media = await self._s.get(Media, media_id)
        if media is None or media.status != "ready":
            return None
        rows = list(
            (
                await self._s.execute(select(MediaVariant).where(MediaVariant.media_id == media_id))
            ).scalars()
        )
        original = next((v for v in rows if v.name == "original"), None)
        if original is None:
            return None
        return MediaInfo(
            id=media.id,
            kind=cast(MediaKind, media.kind),
            mime=media.mime,
            bytes=original.bytes,
            sha256=original.sha256 or "",
            width=media.width,
            height=media.height,
            duration_ms=media.duration_ms,
            page_count=media.page_count,
            variants=sorted(v.name for v in rows),
        )

    async def _asset(self, media_id: UUID, variant: str = "original") -> dict[str, Any] | None:
        row = (
            await self._s.execute(
                select(MediaVariant, Media.status)
                .join(Media, Media.id == MediaVariant.media_id)
                .where(MediaVariant.media_id == media_id, MediaVariant.name == variant)
            )
        ).first()
        if row is None or row[1] != "ready":
            return None
        v = row[0]
        return {"sha256": v.sha256, "bytes": v.bytes, "mime": v.mime}


def _pl(pid: UUID) -> str:
    return f"pl_{pid}"


def _zone(name: object) -> ZoneInfo:
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def _variants(base: dict[str, Any], variants: Collection[PlaylistVariant]) -> dict[str, Any]:
    """Playlists servies : base `pl_<id>` ou variante (durées par défaut, expiration de zone)."""
    out: dict[str, Any] = {}
    for v in sorted(variants, key=lambda v: v.key):
        items = base.get(_pl(v.playlist_id), {"items": []})["items"]
        out[v.key] = (
            base[_pl(v.playlist_id)]
            if v.key == _pl(v.playlist_id) and _pl(v.playlist_id) in base
            else {"items": project_items(items, v.fill_ms, v.until)}
        )
    return out


def _slot(slot: Slot, resolved: ResolvedLayout | None) -> dict[str, Any]:
    base = {"start": slot.start.isoformat(), "end": slot.end.isoformat()}
    if resolved is None:
        off: dict[str, Any] = {**base, "kind": "off", "mode": "black"}
        if slot.override_id is not None:
            off["override_id"] = str(slot.override_id)
        return off
    source: dict[str, Any] = {
        "schedule_id": None if slot.schedule_id is None else str(slot.schedule_id),
        "kind": slot.source,
        "override_id": None if slot.override_id is None else str(slot.override_id),
    }
    if slot.layout_id is not None:
        source["layout_id"] = str(slot.layout_id)
    return {**base, "kind": "play", "layout": resolved.layout, "source": source}
