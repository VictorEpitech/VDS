"""Programmation résolue servie au player (format 1) : chargement, assemblage, révision."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
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
    PlaylistItem,
    PlaylistItemMedia,
    Schedule,
    ScheduleRule,
    Screen,
    Site,
)
from vds_server.diffusion import resolver
from vds_server.diffusion.resolver import ScheduleSpec, Slot

SCHEDULE_FORMAT = 1
DEFAULT_TZ = "Europe/Paris"
SchemaLookup = Callable[[str], dict[str, Any] | None]
InstanceLookup = Callable[[str], Module | None]
PowerLookup = Callable[[UUID, datetime], Any]  # async: -> [{from, to, state}]
TimezoneLookup = Callable[[UUID], Any]  # async: screen_id -> fuseau IANA


async def screen_chain(session: AsyncSession, screen: Screen) -> dict[str, UUID]:
    site = await session.get(Site, screen.site_id)
    if site is None:  # FK : ne peut pas arriver
        raise ApiError("errors.screen_not_found", 404)
    chain = {"organization": site.organization_id, "site": site.id, "screen": screen.id}
    if screen.group_id is not None:
        chain["group"] = screen.group_id
    return chain


async def load_specs(session: AsyncSession, chain: dict[str, UUID]) -> list[ScheduleSpec]:
    """Schedules activés dont la cible est dans la chaîne de l'écran, règles incluses."""
    conds = [
        (Schedule.target_type == level) & (Schedule.target_id == target)
        for level, target in chain.items()
    ]
    rows = list(
        (
            await session.execute(select(Schedule).where(Schedule.enabled.is_(True), or_(*conds)))
        ).scalars()
    )
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
        )
        for r in rows
    ]


async def screen_media_ids(session: AsyncSession, screen_id: UUID) -> set[UUID]:
    """Médias référencés par les programmations actives de l'écran (autorisation de lecture)."""
    screen = await session.get(Screen, screen_id)
    if screen is None or screen.archived_at is not None:
        return set()
    specs = await load_specs(session, await screen_chain(session, screen))
    horizon = datetime.now(UTC).date() - timedelta(days=1)
    playlists = {s.playlist_id for s in specs if s.ends_on is None or s.ends_on >= horizon}
    if not playlists:
        return set()
    q = (
        select(PlaylistItemMedia.media_id)
        .join(PlaylistItem, PlaylistItem.id == PlaylistItemMedia.item_id)
        .where(PlaylistItem.playlist_id.in_(playlists), PlaylistItem.enabled.is_(True))
    )
    return set((await session.execute(q)).scalars())


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
    ) -> None:
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
        tz = _zone(await self._tz_of(screen_id))
        day: date = now.astimezone(tz).date()
        chain = await screen_chain(self._s, screen)
        slots = resolver.resolve(chain, await load_specs(self._s, chain), tz, day)
        playlists, assets, types = await self._playlists(
            {s.playlist_id for s in slots if s.playlist_id}
        )
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
            "slots": [_slot(s, playlists) for s in slots],
            "playlists": playlists,
            "assets": assets,
            "content_types": types,
        }
        if self._power_of is not None:
            body["power"] = await self._power_of(screen_id, now)
        body["revision"] = revision_of(body)
        return body

    async def _playlists(
        self, ids: set[UUID]
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
                built = await self._item(item)
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

    async def _item(self, item: PlaylistItem) -> tuple[dict[str, Any], dict[str, Any]] | None:
        schema = self._item_schema(item.content_type)
        if schema is None:  # content-type absent ou inactif : élément ignoré
            return None
        props = dict(item.props or {})
        refs: list[str] = []
        found: dict[str, Any] = {}
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


def _slot(slot: Slot, playlists: dict[str, Any]) -> dict[str, Any]:
    base = {"start": slot.start.isoformat(), "end": slot.end.isoformat()}
    if slot.schedule_id is None or slot.playlist_id is None:
        return {**base, "kind": "off", "mode": "black"}
    zone = {"id": "main", "x": 0, "y": 0, "w": 1, "h": 1, "playlist": _pl(slot.playlist_id)}
    return {
        **base,
        "kind": "play",
        "layout": {"zones": [zone]},
        "source": {"schedule_id": str(slot.schedule_id)},
    }
