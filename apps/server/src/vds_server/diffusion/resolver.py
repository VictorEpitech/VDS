"""Résolution pure de la programmation d'un écran : créneaux J et J+1 en instants absolus.

Fonction pure (aucun accès base ni horloge) : `resolve(chain, schedules, tz, day)`.
Ordre de choix pour un instant donné : cible la plus spécifique (écran > groupe > site >
organisation), puis `priority` décroissante, puis `updated_at` le plus récent. Aucun actif = `off`.
Les règles hebdomadaires sont en heure murale du fuseau de l'écran ; un schedule sans règle est
actif toute la journée. Alternance et override (lot 4) s'insèrent dans cette même fonction.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
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


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    schedule_id: UUID | None  # None = hors service (off)
    playlist_id: UUID | None


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


def resolve(
    chain: Mapping[str, UUID],
    schedules: Collection[ScheduleSpec],
    tz: tzinfo,
    day: date,
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
                    if start < end:
                        intervals.append((max(start, lo), min(end, hi), spec))
    points = sorted({lo, hi, *(p for a, b, _ in intervals for p in (a, b) if lo <= p <= hi)})
    slots: list[Slot] = []
    for a, b in zip(points, points[1:], strict=False):
        active = [s for start, end, s in intervals if start <= a and end >= b]
        win = max(active, key=_rank) if active else None
        sid = win.id if win else None
        if slots and slots[-1].schedule_id == sid:
            slots[-1] = Slot(slots[-1].start, b, sid, win.playlist_id if win else None)
        else:
            slots.append(Slot(a, b, sid, win.playlist_id if win else None))
    return [
        Slot(s.start.astimezone(tz), s.end.astimezone(tz), s.schedule_id, s.playlist_id)
        for s in slots
    ]
