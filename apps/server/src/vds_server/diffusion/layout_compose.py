"""Composition pure d'un créneau `play` : mise en page (ou playlist implicite) + forçages de zone.

Aucun accès base ni horloge. Entrées : la mise en page chargée (`LayoutSpec`) ou une playlist
(mise en page implicite plein écran, document identique à vds-core 1.8), le créneau, les
forçages de zone actifs. Sortie : le dict `PlaySlot.layout` (vds-core 1.9.0), les variantes de
playlist à construire et les forçages de zone sans effet sur ce créneau (zone absente).

Règles (docs/architecture-mises-en-page.md §3, §3 bis, §5) :
- zones triées `(z, key)` ; zone omise si `active_until <= début` ou `active_from >= fin` ;
- `from` / `until` émis seulement s'ils tombent dans le créneau, et `until` projeté sur les
  éléments (`min`) ; durée des éléments sans durée : zone > défaut résolu ;
- forçage de zone : playlist substituée pendant fenêtre du forçage ∩ créneau ∩ fenêtre de la zone,
  frise sans chevauchement (priorité du forçage d'écran : priority, plus récent, id) ; un forçage
  dont une clé est absente de la mise en page est ignoré pour cette clé et signalé.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

EMPTY_PLAYLIST_KEY = "pl_empty"
DEFAULT_ITEM_DURATION_MS = 10_000
SCALE = 10_000


@dataclass(frozen=True)
class ResolvedDefaults:
    default_item_duration_ms: int = DEFAULT_ITEM_DURATION_MS
    max_exclusive_elements: int = 1


@dataclass(frozen=True)
class ZoneSpec:
    key: str
    x: int  # dix-millièmes
    y: int
    w: int
    h: int
    z: int = 0
    fit: str = "contain"
    playlist_id: UUID | None = None
    default_item_duration_ms: int | None = None
    active_from: datetime | None = None
    active_until: datetime | None = None
    playlist_expires_at: datetime | None = None
    name: str = ""  # jamais servi au player (admin : sélecteur de zone)


@dataclass(frozen=True)
class LayoutSpec:
    id: UUID
    revision: int
    orientation: str
    background_color: str | None
    background_media_id: UUID | None
    zones: tuple[ZoneSpec, ...]


@dataclass(frozen=True)
class ActiveZoneOverride:
    override_id: UUID
    zone_ids: frozenset[str]
    playlist_id: UUID
    starts_at: datetime
    ends_at: datetime | None
    priority: tuple[int, float, str]  # plus grand = prioritaire (règle du forçage d'écran)


@dataclass(frozen=True)
class PlaylistVariant:
    """Playlist telle que servie dans une zone : durées par défaut et expiration projetées."""

    playlist_id: UUID
    fill_ms: int | None = None
    until: datetime | None = None

    @property
    def key(self) -> str:
        if self.fill_ms is None and self.until is None:
            return f"pl_{self.playlist_id}"
        until = "" if self.until is None else iso(self.until)
        return f"pl_{self.playlist_id}@{self.fill_ms or ''}@{until}"


@dataclass(frozen=True)
class ResolvedLayout:
    layout: dict[str, Any]
    variants: frozenset[PlaylistVariant]
    background_media_id: UUID | None = None
    missing_zone_override_ids: frozenset[UUID] = field(default_factory=frozenset)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _frac(v: int) -> float:
    return round(v / SCALE, 4)


def project_items(
    items: Sequence[dict[str, Any]], fill_ms: int | None, until: datetime | None
) -> list[dict[str, Any]]:
    """Copie des éléments avec durée par défaut (si absente) et `until = min(..., until)`."""
    out: list[dict[str, Any]] = []
    for item in items:
        entry = dict(item)
        if fill_ms is not None and entry.get("duration_ms") is None:
            entry["duration_ms"] = fill_ms
        if until is not None:
            current = entry.get("until")
            if current is None or datetime.fromisoformat(str(current)) > until:
                entry["until"] = iso(until)
        out.append(entry)
    return out


def _frieze(
    zone_key: str,
    lo: datetime,
    hi: datetime,
    overrides: Sequence[ActiveZoneOverride],
) -> list[tuple[ActiveZoneOverride, datetime, datetime]]:
    """Frise sans chevauchement des forçages de la zone sur [lo, hi), triée par début."""
    cands = [
        o
        for o in overrides
        if zone_key in o.zone_ids and max(o.starts_at, lo) < min(o.ends_at or hi, hi)
    ]
    if not cands:
        return []
    cuts = {lo, hi}
    for o in cands:
        cuts.update(p for p in (o.starts_at, o.ends_at) if p is not None and lo < p < hi)
    points = sorted(cuts)
    out: list[tuple[ActiveZoneOverride, datetime, datetime]] = []
    for a, b in zip(points, points[1:], strict=False):
        live = [o for o in cands if o.starts_at <= a and (o.ends_at is None or o.ends_at >= b)]
        if not live:
            continue
        win = max(live, key=lambda o: o.priority)
        if out and out[-1][0].override_id == win.override_id and out[-1][2] == a:
            out[-1] = (win, out[-1][1], b)
        else:
            out.append((win, a, b))
    return out


def compose_play_content(
    *,
    playlist_id: UUID | None,
    layout: LayoutSpec | None,
    slot_start: datetime,
    slot_end: datetime,
    defaults: ResolvedDefaults = ResolvedDefaults(),  # noqa: B008
    zone_overrides: Sequence[ActiveZoneOverride] = (),
) -> ResolvedLayout:
    variants: set[PlaylistVariant] = set()
    missing: set[UUID] = set()

    def zone_overrides_of(key: str, lo: datetime, hi: datetime, fill: int | None,
                          until: datetime | None) -> list[dict[str, Any]]:  # fmt: skip
        windows: list[dict[str, Any]] = []
        for o, a, b in _frieze(key, lo, hi, zone_overrides):
            v = PlaylistVariant(o.playlist_id, fill, until)
            variants.add(v)
            windows.append(
                {
                    "override_id": str(o.override_id),
                    "playlist": v.key,
                    "from": iso(a),
                    "until": iso(b),
                }
            )
        return windows

    if layout is None:
        if playlist_id is None:
            raise ValueError("playlist_id or layout required")
        main = PlaylistVariant(playlist_id)
        variants.add(main)
        zone: dict[str, Any] = {
            "id": "main", "x": 0, "y": 0, "w": 1, "h": 1, "playlist": main.key,
        }  # fmt: skip
        if overrides := zone_overrides_of("main", slot_start, slot_end, None, None):
            zone["overrides"] = overrides
        missing = _missing(zone_overrides, {"main"}, slot_start, slot_end)
        return ResolvedLayout({"zones": [zone]}, frozenset(variants), None, frozenset(missing))

    zones: list[dict[str, Any]] = []
    for z in sorted(layout.zones, key=lambda s: (s.z, s.key)):
        if z.active_until is not None and z.active_until <= slot_start:
            continue
        if z.active_from is not None and z.active_from >= slot_end:
            continue
        from_ = z.active_from if z.active_from and z.active_from > slot_start else None
        until = z.active_until if z.active_until and z.active_until < slot_end else None
        fill = z.default_item_duration_ms or defaults.default_item_duration_ms
        pl_until = until
        pl_key = EMPTY_PLAYLIST_KEY
        exp = z.playlist_expires_at
        if z.playlist_id is not None and not (exp is not None and exp <= slot_start):
            if exp is not None and exp < slot_end and (pl_until is None or exp < pl_until):
                pl_until = exp
            v = PlaylistVariant(z.playlist_id, fill, pl_until)
            variants.add(v)
            pl_key = v.key
        entry: dict[str, Any] = {
            "id": z.key, "x": _frac(z.x), "y": _frac(z.y), "w": _frac(z.w), "h": _frac(z.h),
            "playlist": pl_key, "z": z.z, "fit": z.fit,
        }  # fmt: skip
        if from_ is not None:
            entry["from"] = iso(from_)
        if until is not None:
            entry["until"] = iso(until)
        lo, hi = from_ or slot_start, until or slot_end
        if windows := zone_overrides_of(z.key, lo, hi, fill, until):
            entry["overrides"] = windows
        zones.append(entry)
    missing = _missing(zone_overrides, {z.key for z in layout.zones}, slot_start, slot_end)
    doc: dict[str, Any] = {
        "zones": zones,
        "id": str(layout.id),
        "revision": layout.revision,
        "orientation": layout.orientation,
    }
    background: dict[str, Any] = {}
    if layout.background_color:
        background["color"] = layout.background_color
    if background or layout.background_media_id is not None:
        doc["background"] = background
    return ResolvedLayout(doc, frozenset(variants), layout.background_media_id, frozenset(missing))


def _missing(
    overrides: Sequence[ActiveZoneOverride],
    keys: set[str],
    lo: datetime,
    hi: datetime,
) -> set[UUID]:
    return {
        o.override_id
        for o in overrides
        if max(o.starts_at, lo) < min(o.ends_at or hi, hi) and not o.zone_ids <= keys
    }
