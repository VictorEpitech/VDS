"""Résolution pure de l'alimentation d'un écran : créneaux `on`/`off` J et J+1, instants absolus.

Fonction pure (aucun accès base ni horloge). Pour chaque jour local du fuseau :
1. sans plan (`plan_rules is None`) : allumé toute la journée (comportement lot 2) ;
2. avec plan : allumé sur les plages des règles du jour de semaine, éteint ailleurs ;
3. jour présent dans un calendrier rattaché : éteint toute la journée (les règles sont ignorées) ;
4. exceptions : par instant, l'exception du niveau le plus spécifique (écran > groupe > site >
   organisation) l'emporte sur le plan et les calendriers ; à niveau égal, `off` l'emporte.
Les bornes sont des heures murales converties en instants : un jour de changement d'heure dure
23 h ou 25 h, et les créneaux contigus de même état sont fusionnés (minuit n'est pas une borne).
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Literal

from vds_server.diffusion.resolver import DAY_MINUTES, SPECIFICITY, WINDOW_DAYS, wall_to_instant

State = Literal["on", "off"]


@dataclass(frozen=True)
class PlanRule:
    weekdays: int  # masque : lundi = 1 ... dimanche = 64
    start_minute: int
    end_minute: int  # plage ALLUMÉE, 1440 = minuit de fin


@dataclass(frozen=True)
class ExceptionSpec:
    scope_level: str
    starts_on: date
    ends_on: date
    state: State
    start_minute: int | None = None  # None = journée entière
    end_minute: int | None = None


@dataclass(frozen=True)
class PowerSlot:
    start: datetime
    end: datetime
    state: State


# (rang, état) ; rang : 0 = fond, 1 = plan/calendrier, 2+ = exceptions (niveau, puis off)
_Layer = tuple[datetime, datetime, tuple[int, int], State]


def _day_layers(
    tz: tzinfo,
    d: date,
    plan_rules: Sequence[PlanRule] | None,
    calendar_days: Collection[date],
    exceptions: Collection[ExceptionSpec],
) -> list[_Layer]:
    lo, hi = wall_to_instant(tz, d, 0), wall_to_instant(tz, d, DAY_MINUTES)
    layers: list[_Layer] = []
    if plan_rules is None:
        layers.append((lo, hi, (0, 0), "on"))
    else:
        layers.append((lo, hi, (0, 0), "off"))
        if d in calendar_days:
            layers.append((lo, hi, (1, 0), "off"))
        else:
            for r in plan_rules:
                if r.weekdays & (1 << d.weekday()):
                    layers.append(
                        (
                            wall_to_instant(tz, d, r.start_minute),
                            wall_to_instant(tz, d, r.end_minute),
                            (1, 0),
                            "on",
                        )
                    )
    for e in exceptions:
        if e.scope_level not in SPECIFICITY or not e.starts_on <= d <= e.ends_on:
            continue
        a = wall_to_instant(tz, d, 0 if e.start_minute is None else e.start_minute)
        b = wall_to_instant(tz, d, DAY_MINUTES if e.end_minute is None else e.end_minute)
        if a < b:
            rank = (2 + SPECIFICITY[e.scope_level], 1 if e.state == "off" else 0)
            layers.append((a, b, rank, e.state))
    return layers


def resolve(
    plan_rules: Sequence[PlanRule] | None,
    calendar_days: Collection[date],
    exceptions: Collection[ExceptionSpec],
    tz: tzinfo,
    day: date,
) -> list[PowerSlot]:
    """Créneaux contigus couvrant [minuit local de `day`, minuit local de `day`+2 j)."""
    lo = wall_to_instant(tz, day, 0)
    hi = wall_to_instant(tz, day + timedelta(days=WINDOW_DAYS), 0)
    layers: list[_Layer] = []
    for offset in range(WINDOW_DAYS):
        layers += _day_layers(
            tz, day + timedelta(days=offset), plan_rules, calendar_days, exceptions
        )
    points = sorted({lo, hi, *(p for a, b, _, _ in layers for p in (a, b) if lo <= p <= hi)})
    slots: list[PowerSlot] = []
    for a, b in zip(points, points[1:], strict=False):
        covering = [(rank, st) for s, e, rank, st in layers if s <= a and e >= b]
        state: State = max(covering, key=lambda c: c[0])[1] if covering else "on"
        if slots and slots[-1].state == state:
            slots[-1] = PowerSlot(slots[-1].start, b, state)
        else:
            slots.append(PowerSlot(a, b, state))
    return [PowerSlot(s.start.astimezone(tz), s.end.astimezone(tz), s.state) for s in slots]


def to_wire(slots: Sequence[PowerSlot]) -> list[dict[str, str]]:
    """Format `{from, to, state}` (ISO 8601 avec décalage) servi au player et à l'agent."""
    return [{"from": s.start.isoformat(), "to": s.end.isoformat(), "state": s.state} for s in slots]


def state_at(slots: Sequence[PowerSlot], at: datetime) -> State:
    instant = at.astimezone(UTC)
    for s in slots:
        if s.start <= instant < s.end:
            return s.state
    return "on"
