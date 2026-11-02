"""Résolution pure de la programmation d'un écran : créneaux J et J+1 en instants absolus.

Fonction pure (aucun accès base ni horloge) : `resolve(chain, schedules, tz, day)`.
Ordre de choix pour un instant donné : cible la plus spécifique (écran > groupe > site >
organisation), puis `priority` décroissante, puis `updated_at` le plus récent. Aucun actif = `off`.
Les règles hebdomadaires sont en heure murale du fuseau de l'écran ; un schedule sans règle est
actif toute la journée.

Forçage (`OverrideSpec`, vds-core 1.7.0) : sur chaque segment, un forçage actif ciblant la chaîne
de l'écran l'emporte sur la programmation, quelle que soit la spécificité de la cible. Entre
forçages : extinction d'abord (un `off` bat toute playlist), puis `priority` décroissante,
puis le plus récent (`created_at`), puis l'id. Un forçage
`respect_scheduled_off` ne s'applique pas là où la programmation est hors plage (`off`).
Une programmation avec `until` (expiration de sa playlist) est inactive à partir de cet instant.
Un forçage sans playlist (`playlist_id is None`) force l'extinction.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Literal
from uuid import UUID

SPECIFICITY = {"organization": 0, "site": 1, "group": 2, "screen": 3}
WINDOW_DAYS = 2  # J et J+1
DAY_MINUTES = 1440


@dataclass(frozen=True)
class Rule:
    weekdays: int  # masque : lundi = 1 ... dimanche = 64
    start_minute: int
    end_minute: int  # 1440 = minuit de fin


@dataclass(frozen=True)
class ScheduleSpec:
    id: UUID
    playlist_id: UUID
    target_type: str
    target_id: UUID
    priority: int
    updated_at: datetime
    starts_on: date | None = None
    ends_on: date | None = None
    enabled: bool = True
    rules: tuple[Rule, ...] = ()
    until: datetime | None = None  # UTC : expiration de la playlist, point de découpe


@dataclass(frozen=True)
class OverrideSpec:
    id: UUID
    target_type: str
    target_id: UUID
    starts_at: datetime  # UTC
    ends_at: datetime  # UTC, déjà tronqué à ended_at si arrêté
    playlist_id: UUID | None  # None = forçage d'extinction
    priority: int
    created_at: datetime
    respect_scheduled_off: bool = False


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    schedule_id: UUID | None  # None = hors service (off) ou forçage
    playlist_id: UUID | None
    override_id: UUID | None = None

    @property
    def source(self) -> Literal["schedule", "override", "off"]:
        if self.override_id is not None:
            return "override"
        return "off" if self.schedule_id is None else "schedule"


def wall_to_instant(tz: tzinfo, d: date, minute: int) -> datetime:
    """Heure murale (minutes depuis minuit, 1440 = minuit suivant) -> instant UTC."""
    if minute >= DAY_MINUTES:
        d, minute = d + timedelta(days=1), minute - DAY_MINUTES
    return datetime.combine(d, time(minute // 60, minute % 60), tzinfo=tz).astimezone(UTC)


def _targets(chain: Mapping[str, UUID], spec: ScheduleSpec) -> bool:
    return spec.target_type in SPECIFICITY and chain.get(spec.target_type) == spec.target_id


def _rank(spec: ScheduleSpec) -> tuple[int, int, float, str]:
    # plus grand = prioritaire ; l'id départage de façon déterministe
    return (
        SPECIFICITY[spec.target_type],
        spec.priority,
        spec.updated_at.timestamp(),
        str(spec.id),
    )


def _override_rank(spec: OverrideSpec) -> tuple[int, int, float, str]:
    # un forçage d'extinction (réservé aux admins) bat toujours un forçage de playlist
    return (
        1 if spec.playlist_id is None else 0,
        spec.priority,
        spec.created_at.timestamp(),
        str(spec.id),
    )


def winning_override(
    chain: Mapping[str, UUID],
    overrides: Collection[OverrideSpec],
    a: datetime,
    b: datetime,
    *,
    schedule_off: bool,
) -> OverrideSpec | None:
    """Forçage gagnant sur le segment [a, b) (aucune borne de forçage ne tombe à l'intérieur)."""
    active = [
        o
        for o in overrides
        if o.target_type in SPECIFICITY
        and chain.get(o.target_type) == o.target_id
        and o.starts_at <= a
        and o.ends_at >= b
        and not (o.respect_scheduled_off and schedule_off)
    ]
    return max(active, key=_override_rank) if active else None


def resolve(
    chain: Mapping[str, UUID],
    schedules: Collection[ScheduleSpec],
    tz: tzinfo,
    day: date,
    overrides: Collection[OverrideSpec] = (),
) -> list[Slot]:
    """Créneaux contigus couvrant [minuit local de `day`, minuit local de `day`+2 j)."""
    lo = wall_to_instant(tz, day, 0)
    hi = wall_to_instant(tz, day + timedelta(days=WINDOW_DAYS), 0)
    intervals: list[tuple[datetime, datetime, ScheduleSpec]] = []
    for spec in schedules:
        if not spec.enabled or not _targets(chain, spec):
            continue
        for offset in range(WINDOW_DAYS):
            d = day + timedelta(days=offset)
            if (spec.starts_on and d < spec.starts_on) or (spec.ends_on and d > spec.ends_on):
                continue
            rules = spec.rules or (Rule(127, 0, DAY_MINUTES),)
            for rule in rules:
                if rule.weekdays & (1 << d.weekday()):
                    start = wall_to_instant(tz, d, rule.start_minute)
                    end = wall_to_instant(tz, d, rule.end_minute)
                    if spec.until is not None:
                        end = min(end, spec.until)
                    if start < end:
                        intervals.append((max(start, lo), min(end, hi), spec))
    mine = [
        o
        for o in overrides
        if o.target_type in SPECIFICITY
        and chain.get(o.target_type) == o.target_id
        and o.starts_at < o.ends_at
        and o.ends_at > lo
        and o.starts_at < hi
    ]
    bounds = (p for a, b, _ in intervals for p in (a, b))
    obounds = (p for o in mine for p in (o.starts_at, o.ends_at))
    points = sorted({lo, hi, *(p for p in (*bounds, *obounds) if lo <= p <= hi)})
    slots: list[Slot] = []
    for a, b in zip(points, points[1:], strict=False):
        active = [s for start, end, s in intervals if start <= a and end >= b]
        win = max(active, key=_rank) if active else None
        forced = winning_override(chain, mine, a, b, schedule_off=win is None)
        if forced is not None:
            cur = Slot(a, b, None, forced.playlist_id, forced.id)
        else:
            cur = Slot(a, b, win.id if win else None, win.playlist_id if win else None)
        last = slots[-1] if slots else None
        if last and (last.schedule_id, last.override_id) == (cur.schedule_id, cur.override_id):
            slots[-1] = Slot(last.start, b, last.schedule_id, last.playlist_id, last.override_id)
        else:
            slots.append(cur)
    return [
        Slot(
            s.start.astimezone(tz),
            s.end.astimezone(tz),
            s.schedule_id,
            s.playlist_id,
            s.override_id,
        )
        for s in slots
    ]
