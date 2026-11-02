"""Résolveur pur : forçage (priorités, hors plage, fin en cours de créneau, DST, minuit Paris)."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from vds_server.diffusion.resolver import (
    OverrideSpec,
    Rule,
    ScheduleSpec,
    Slot,
    resolve,
    wall_to_instant,
)
from vds_server.power.resolver import PowerSlot, apply_overrides

PARIS = ZoneInfo("Europe/Paris")
ORG, SITE, GROUP, SCREEN = (uuid4() for _ in range(4))
CHAIN = {"organization": ORG, "site": SITE, "group": GROUP, "screen": SCREEN}
PL_S, PL_O1, PL_O2 = uuid4(), uuid4(), uuid4()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return wall_to_instant(PARIS, day, hh * 60 + mm)


def sched(target_type: str = "screen", target_id: UUID = SCREEN, **kw: object) -> ScheduleSpec:
    return ScheduleSpec(
        id=uuid4(),
        playlist_id=PL_S,
        target_type=target_type,
        target_id=target_id,
        priority=0,
        updated_at=T0,
        **kw,  # type: ignore[arg-type]
    )


def ov(
    start: datetime,
    end: datetime,
    *,
    target_type: str = "site",
    target_id: UUID = SITE,
    playlist: UUID | None = PL_O1,
    priority: int = 0,
    created: datetime = T0,
    respect: bool = False,
) -> OverrideSpec:
    return OverrideSpec(
        id=uuid4(),
        target_type=target_type,
        target_id=target_id,
        starts_at=start,
        ends_at=end,
        playlist_id=playlist,
        priority=priority,
        created_at=created,
        respect_scheduled_off=respect,
    )


D = date(2026, 3, 10)


def test_override_wins_over_most_specific_schedule() -> None:
    o = ov(at(D, 10), at(D, 12), target_type="organization", target_id=ORG)
    slots = resolve(CHAIN, [sched()], PARIS, D, [o])
    assert [(s.source, s.playlist_id) for s in slots] == [
        ("schedule", PL_S),
        ("override", PL_O1),
        ("schedule", PL_S),
    ]
    mid = slots[1]
    assert (mid.start, mid.end) == (at(D, 10), at(D, 12)) and mid.override_id == o.id
    assert mid.schedule_id is None
    assert slots[0].end == mid.start and slots[2].start == mid.end  # sans trou


def test_other_target_ignored() -> None:
    o = ov(at(D, 10), at(D, 12), target_type="screen", target_id=uuid4())
    assert [s.source for s in resolve(CHAIN, [sched()], PARIS, D, [o])] == ["schedule"]


def test_latest_created_wins_then_priority() -> None:
    old = ov(at(D, 9), at(D, 13), playlist=PL_O1, created=T0)
    new = ov(at(D, 10), at(D, 11), playlist=PL_O2, created=T0 + timedelta(hours=1))
    slots = resolve(CHAIN, [], PARIS, D, [old, new])
    got = [(s.override_id, s.playlist_id) for s in slots if s.override_id]
    assert got == [(old.id, PL_O1), (new.id, PL_O2), (old.id, PL_O1)]
    high = ov(at(D, 10), at(D, 11), playlist=PL_O1, priority=5, created=T0)
    low_new = ov(at(D, 9), at(D, 12), playlist=PL_O2, created=T0 + timedelta(days=1))
    mid = next(s for s in resolve(CHAIN, [], PARIS, D, [high, low_new]) if s.start == at(D, 10))
    assert mid.override_id == high.id  # priorité avant ancienneté


def test_off_gap_replaced_unless_respect() -> None:
    plan = [sched(rules=(Rule(127, 8 * 60, 18 * 60),))]
    o = ov(at(D, 6), at(D, 20))
    slots = resolve(CHAIN, plan, PARIS, D, [o])
    covered = [s for s in slots if s.start >= at(D, 6) and s.end <= at(D, 20)]
    assert covered and all(s.source == "override" for s in covered)
    r = ov(at(D, 6), at(D, 20), respect=True)
    got = [(s.source, s.start, s.end) for s in resolve(CHAIN, plan, PARIS, D, [r])]
    assert ("off", at(D, 0), at(D, 8)) in got  # le forçage respecte le hors plage
    assert ("override", at(D, 8), at(D, 18)) in got
    assert ("off", at(D, 18), at(D + timedelta(days=1), 8)) in got


def test_off_kind_forces_black() -> None:
    o = ov(at(D, 10), at(D, 11), playlist=None)
    s = next(x for x in resolve(CHAIN, [sched()], PARIS, D, [o]) if x.override_id)
    assert s.playlist_id is None and s.source == "override"


def test_end_mid_slot_returns_to_schedule() -> None:
    o = ov(at(D, 0), at(D, 14, 30))
    slots = resolve(CHAIN, [sched(rules=(Rule(127, 8 * 60, 20 * 60),))], PARIS, D, [o])
    after = next(s for s in slots if s.start == at(D, 14, 30))
    assert after.source == "schedule" and after.end == at(D, 20)


def test_end_beyond_window_clipped() -> None:
    o = ov(at(D, 12), at(D, 12) + timedelta(days=10))
    slots = resolve(CHAIN, [], PARIS, D, [o])
    assert slots[-1].source == "override" and slots[-1].end == at(D + timedelta(days=2), 0)


def test_override_before_window_ignored() -> None:
    o = ov(at(D, 0) - timedelta(days=3), at(D, 0) - timedelta(days=1))
    assert all(s.override_id is None for s in resolve(CHAIN, [], PARIS, D, [o]))


def test_midnight_and_dst_paris() -> None:
    spring = date(2026, 3, 28)  # changement d'heure le 29 mars (23 h -> pas ici, J+1 = 23 h)
    o = ov(at(spring, 22), at(spring + timedelta(days=1), 3, 30))
    slots = resolve(CHAIN, [], PARIS, spring, [o])
    forced = [s for s in slots if s.override_id]
    assert len(forced) == 1  # minuit n'est pas une borne : un seul créneau
    assert forced[0].start == at(spring, 22) and forced[0].end == at(
        spring + timedelta(days=1), 3, 30
    )
    real = forced[0].end.astimezone(UTC) - forced[0].start.astimezone(UTC)
    assert real == timedelta(hours=4, minutes=30)  # 5 h 30 murales - 1 h de changement d'heure
    assert slots[0].start.utcoffset() == timedelta(hours=1)
    assert slots[-1].end.utcoffset() == timedelta(hours=2)
    autumn = date(2026, 10, 24)
    o2 = ov(at(autumn, 23), at(autumn + timedelta(days=1), 4))
    f = next(s for s in resolve(CHAIN, [], PARIS, autumn, [o2]) if s.override_id)
    assert f.end.astimezone(UTC) - f.start.astimezone(UTC) == timedelta(hours=6)  # jour de 25 h


def test_slot_source_property_and_contiguity() -> None:
    assert Slot(T0, T0, None, None).source == "off"
    assert Slot(T0, T0, uuid4(), PL_S).source == "schedule"
    o = ov(at(D, 0), at(D, 12))
    o2 = ov(at(D, 12), at(D, 14), created=T0 + timedelta(hours=1))
    slots = resolve(CHAIN, [], PARIS, D, [o, o2])
    assert [s.override_id for s in slots[:2]] == [o.id, o2.id]  # deux forçages = deux créneaux


# --- alimentation : un forçage rallume (sauf respect) ou éteint -------------------------
def _pw(d: date, h1: int, h2: int, state: str) -> PowerSlot:
    return PowerSlot(at(d, h1), at(d, h2), state)  # type: ignore[arg-type]


def test_power_overlay() -> None:
    base = [_pw(D, 0, 8, "off"), _pw(D, 8, 18, "on"), _pw(D, 18, 24, "off")]
    o = ov(at(D, 6), at(D, 20))
    out = apply_overrides(base, CHAIN, [o], PARIS)
    assert [(s.state, s.start, s.end) for s in out] == [
        ("off", at(D, 0), at(D, 6)),
        ("on", at(D, 6), at(D, 20)),
        ("off", at(D, 20), at(D, 24)),
    ]
    # « premier off » (agent) : aucun off ne recouvre le forçage
    assert not any(s.state == "off" and s.start < at(D, 20) and s.end > at(D, 6) for s in out)
    kept = apply_overrides(base, CHAIN, [ov(at(D, 6), at(D, 20), respect=True)], PARIS)
    assert [(s.state, s.start) for s in kept] == [
        ("off", at(D, 0)),
        ("on", at(D, 8)),
        ("off", at(D, 18)),
    ]
    black = apply_overrides(base, CHAIN, [ov(at(D, 9), at(D, 10), playlist=None)], PARIS)
    assert [s.state for s in black] == ["off", "on", "off", "on", "off"]
