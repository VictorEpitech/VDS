"""Conflits et marqueurs d'un écran, calculés à partir des créneaux expliqués du résolveur.

Pur : aucune lecture base. Entrée = `ResolveInput` + créneaux de `resolve(explain=True)`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from vds_server.diffusion.resolver import (
    SPECIFICITY,
    Ref,
    ResolveInput,
    Slot,
    _override_ref,
    _spec_ref,
)

ConflictKind = Literal[
    "never_plays", "partially_masked", "ambiguous_tie", "content_expires", "content_empty", "gap"
]
MarkerKind = Literal[
    "playlist_expires", "media_expires", "override_ends", "schedule_starts", "schedule_ends"
]
_ELIGIBLE = {"override_active", "higher_priority", "more_specific", "tie_break"}


@dataclass(frozen=True)
class Conflict:
    kind: ConflictKind
    severity: Literal["info", "warning"]
    source: Ref
    other: Ref | None
    first_at: datetime
    occurrences: int


@dataclass(frozen=True)
class Marker:
    at: datetime
    kind: MarkerKind
    ref_id: UUID  # playlist, média, forçage ou programmation (le nom est résolu par l'API)
    impact: Literal["gap", "fallback", "none"]


def _is_off(s: Slot) -> bool:
    return s.schedule_id is None and s.override_id is None


def _impact(slots: list[Slot], at: datetime) -> Literal["gap", "fallback", "none"]:
    after = next((s for s in slots if s.start <= at < s.end), None)
    if after is None:
        return "none"
    if after.override_id is not None:
        return "none"
    return "gap" if after.schedule_id is None else "fallback"


def conflicts(
    inp: ResolveInput, slots: list[Slot], *, empty_playlists: frozenset[UUID] = frozenset()
) -> list[Conflict]:
    mine = {
        s.id: s for s in inp.schedules if s.enabled and inp.chain.get(s.target_type) == s.target_id
    }
    won: dict[UUID, int] = {}
    masked_by: dict[UUID, tuple[Ref, datetime]] = {}
    masked_n: dict[UUID, int] = {}
    first: dict[UUID, datetime] = {}
    ties: dict[tuple[UUID, UUID], list[datetime]] = {}
    expired: dict[UUID, list[Slot]] = {}
    empty: dict[UUID, list[datetime]] = {}
    for sl in slots:
        winner = sl.schedule_id
        if winner is not None:
            won[winner] = won.get(winner, 0) + 1
            first.setdefault(winner, sl.start)
        if sl.playlist_id in empty_playlists and not _is_off(sl):
            key = sl.schedule_id or sl.override_id
            if key is not None:
                empty.setdefault(key, []).append(sl.start)
        for lo in sl.losers:
            if lo.ref.kind != "schedule":
                continue
            if lo.reason in _ELIGIBLE:
                first.setdefault(lo.ref.id, sl.start)
                masked_n[lo.ref.id] = masked_n.get(lo.ref.id, 0) + 1
                if lo.ref.id not in masked_by:
                    by = _winner_ref(inp, sl)
                    if by is not None:
                        masked_by[lo.ref.id] = (by, sl.start)
            if lo.reason == "tie_break" and winner is not None:
                ties.setdefault((winner, lo.ref.id), []).append(sl.start)
            if lo.reason == "content_expired":
                expired.setdefault(lo.ref.id, []).append(sl)
    out: list[Conflict] = []
    for sid in sorted(masked_n, key=str):
        spec = mine.get(sid)
        if spec is None:
            continue
        by_ref, at = masked_by[sid] if sid in masked_by else (None, first[sid])
        kind: ConflictKind = "never_plays" if won.get(sid, 0) == 0 else "partially_masked"
        out.append(Conflict(kind, "info", _spec_ref(spec), by_ref, at, masked_n[sid]))
    for (w, lo_id), starts in sorted(ties.items(), key=lambda kv: str(kv[0])):
        if w in mine and lo_id in mine:
            out.append(
                Conflict("ambiguous_tie", "warning", _spec_ref(mine[w]), _spec_ref(mine[lo_id]),
                         starts[0], len(starts))
            )  # fmt: skip
    for sid, lst in sorted(expired.items(), key=lambda kv: str(kv[0])):
        if sid in mine:
            gap = any(_is_off(x) for x in lst)
            out.append(
                Conflict("content_expires", "warning" if gap else "info", _spec_ref(mine[sid]),
                         None, lst[0].start, len(lst))
            )  # fmt: skip
    for key, starts in sorted(empty.items(), key=lambda kv: str(kv[0])):
        ref = _slot_ref(inp, key)
        if ref is not None:
            out.append(Conflict("content_empty", "warning", ref, None, starts[0], len(starts)))
    for i in range(1, len(slots) - 1):
        cur, prev, nxt = slots[i], slots[i - 1], slots[i + 1]
        same_day = cur.start.date() == (cur.end - timedelta(microseconds=1)).date()
        if _is_off(cur) and not _is_off(prev) and not _is_off(nxt) and same_day:
            before, after = _winner_ref(inp, prev), _winner_ref(inp, nxt)
            if before is not None:
                out.append(Conflict("gap", "info", before, after, cur.start, 1))
    return out


def _winner_ref(inp: ResolveInput, sl: Slot) -> Ref | None:
    return _slot_ref(inp, sl.override_id if sl.override_id is not None else sl.schedule_id)


def _slot_ref(inp: ResolveInput, key: UUID | None) -> Ref | None:
    if key is None:
        return None
    for s in inp.schedules:
        if s.id == key:
            return _spec_ref(s)
    for o in inp.overrides:
        if o.id == key:
            return _override_ref(o)
    return None


def markers(
    inp: ResolveInput,
    slots: list[Slot],
    *,
    media_until: Iterable[tuple[UUID, datetime]] = (),
) -> list[Marker]:
    """Instants remarquables dans la fenêtre. `media_until` : (média, fin d'éligibilité)."""
    lo, hi = slots[0].start, slots[-1].end
    out: list[Marker] = []
    for s in inp.schedules:
        if s.enabled and inp.chain.get(s.target_type) == s.target_id:
            if s.until is not None and lo <= s.until < hi:
                out.append(
                    Marker(s.until, "playlist_expires", s.playlist_id, _impact(slots, s.until))
                )
    for o in inp.overrides:
        if SPECIFICITY.get(o.target_type) is not None and lo < o.ends_at < hi:
            out.append(Marker(o.ends_at, "override_ends", o.id, _impact(slots, o.ends_at)))
    for media_id, at in media_until:
        if lo <= at < hi:
            out.append(Marker(at, "media_expires", media_id, "none"))
    for i, sl in enumerate(slots):
        if sl.schedule_id is None:
            continue
        if i == 0 or slots[i - 1].schedule_id != sl.schedule_id:
            out.append(Marker(sl.start, "schedule_starts", sl.schedule_id, "none"))
        if i == len(slots) - 1 or slots[i + 1].schedule_id != sl.schedule_id:
            out.append(Marker(sl.end, "schedule_ends", sl.schedule_id, "none"))
    return sorted(out, key=lambda m: (m.at, m.kind, str(m.ref_id)))


__all__ = ["Conflict", "Marker", "conflicts", "markers"]
