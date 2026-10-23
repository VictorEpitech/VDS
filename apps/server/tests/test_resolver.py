"""Résolveur pur de programmation : priorité, spécificité, règles, fuseau, heure d'hiver."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from vds_server.diffusion.resolver import Rule, ScheduleSpec, Slot, resolve

PARIS = ZoneInfo("Europe/Paris")
ORG, SITE, GROUP, SCREEN = (uuid4() for _ in range(4))
CHAIN = {"organization": ORG, "site": SITE, "group": GROUP, "screen": SCREEN}
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ALL = 127


def spec(
    level: str = "screen",
    *,
    rules: tuple[Rule, ...] = (),
    priority: int = 0,
    updated: datetime = T0,
    target: UUID | None = None,
    **kw: object,
) -> ScheduleSpec:
    return ScheduleSpec(
        id=uuid4(),
        playlist_id=uuid4(),
        target_type=level,
        target_id=target or CHAIN[level],
        priority=priority,
        updated_at=updated,
        rules=rules,
        **kw,  # type: ignore[arg-type]
    )


def hours(a: int, b: int, weekdays: int = ALL) -> Rule:
    return Rule(weekdays, a * 60, b * 60)


def span(a: datetime, b: datetime) -> timedelta:
    """Durée réelle (la soustraction de deux datetimes du même fuseau est en heure murale)."""
    return b.astimezone(UTC) - a.astimezone(UTC)


def assert_contiguous(slots: list[Slot], day: date, tz: ZoneInfo = PARIS) -> None:
    lo = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    hi = datetime.combine(day + timedelta(days=2), datetime.min.time(), tzinfo=tz)
    assert slots[0].start == lo and slots[-1].end == hi
    for a, b in zip(slots, slots[1:], strict=False):
        assert a.end == b.start and a.schedule_id != b.schedule_id


def test_empty_is_off_for_two_days() -> None:
    slots = resolve(CHAIN, [], PARIS, date(2026, 10, 23))
    assert len(slots) == 1 and slots[0].schedule_id is None and slots[0].playlist_id is None
    assert_contiguous(slots, date(2026, 10, 23))


def test_no_rule_means_all_day_single_slot() -> None:
    s = spec()
    slots = resolve(CHAIN, [s], PARIS, date(2026, 10, 23))
    assert [x.schedule_id for x in slots] == [s.id]
    assert_contiguous(slots, date(2026, 10, 23))


def test_rules_make_gaps_off() -> None:
    s = spec(rules=(hours(8, 19),))
    slots = resolve(CHAIN, [s], PARIS, date(2026, 10, 23))
    kinds = [x.schedule_id is not None for x in slots]
    assert kinds == [False, True, False, True, False]
    assert slots[1].start == datetime(2026, 10, 23, 8, tzinfo=PARIS)
    assert slots[1].end == datetime(2026, 10, 23, 19, tzinfo=PARIS)
    assert_contiguous(slots, date(2026, 10, 23))


def test_priority_then_updated_at() -> None:
    low, high = spec(priority=1), spec(priority=5)
    assert resolve(CHAIN, [low, high], PARIS, date(2026, 10, 23))[0].schedule_id == high.id
    old, new = spec(updated=T0), spec(updated=T0 + timedelta(days=1))
    assert resolve(CHAIN, [old, new], PARIS, date(2026, 10, 23))[0].schedule_id == new.id


def test_specificity_beats_priority() -> None:
    order = ["organization", "site", "group", "screen"]
    specs = [spec(level, priority=1000 - i * 100) for i, level in enumerate(order)]
    got = resolve(CHAIN, specs, PARIS, date(2026, 10, 23))[0].schedule_id
    assert got == specs[3].id
    without_screen = specs[:3]
    assert resolve(CHAIN, without_screen, PARIS, date(2026, 10, 23))[0].schedule_id == specs[2].id


def test_partial_override_splits_slots() -> None:
    base = spec("site")
    cover = spec("screen", rules=(hours(10, 12),))
    slots = resolve(CHAIN, [base, cover], PARIS, date(2026, 10, 23))
    assert [x.schedule_id for x in slots] == [base.id, cover.id, base.id, cover.id, base.id]
    assert_contiguous(slots, date(2026, 10, 23))


def test_other_targets_disabled_and_dates_ignored() -> None:
    other = spec("screen", target=uuid4())
    off = spec(enabled=False)
    not_yet = spec(starts_on=date(2026, 10, 24))
    over = spec(ends_on=date(2026, 10, 22))
    slots = resolve(CHAIN, [other, off, not_yet, over], PARIS, date(2026, 10, 23))
    assert [x.schedule_id for x in slots] == [None, not_yet.id]
    assert slots[1].start == datetime(2026, 10, 24, tzinfo=PARIS)


def test_weekday_mask() -> None:
    # 23 oct. 2026 = vendredi (bit 16), 24 = samedi (bit 32)
    s = spec(rules=(Rule(32, 0, 1440),))
    slots = resolve(CHAIN, [s], PARIS, date(2026, 10, 23))
    assert [x.schedule_id for x in slots] == [None, s.id]


def test_screen_timezone_applies() -> None:
    tokyo = ZoneInfo("Asia/Tokyo")
    s = spec(rules=(hours(8, 9),))
    slot = resolve(CHAIN, [s], tokyo, date(2026, 10, 23))[1]
    assert slot.start == datetime(2026, 10, 23, 8, tzinfo=tokyo)
    assert slot.start.astimezone(UTC).hour == 23  # 08:00 JST = 23:00Z la veille


def test_fall_back_2026_10_25_europe_paris() -> None:
    day = date(2026, 10, 24)
    s = spec(rules=(hours(8, 19),))
    slots = resolve(CHAIN, [s], PARIS, day)
    assert_contiguous(slots, day)
    assert slots[-1].end.utcoffset() == timedelta(hours=1)
    assert slots[0].start.utcoffset() == timedelta(hours=2)
    play = [x for x in slots if x.schedule_id]
    assert play[0].start.isoformat() == "2026-10-24T08:00:00+02:00"
    assert play[1].start.isoformat() == "2026-10-25T08:00:00+01:00"
    assert play[1].end.isoformat() == "2026-10-25T19:00:00+01:00"
    total = span(slots[0].start, slots[-1].end)
    assert total == timedelta(hours=49)  # le 25 octobre dure 25 h
    # journée entière le 25 : 25 h réelles, un seul créneau
    whole = spec(rules=(Rule(ALL, 0, 1440),))
    one = resolve(CHAIN, [whole], PARIS, day)
    assert len(one) == 1 and span(one[0].start, one[0].end) == timedelta(hours=49)
    # une plage dans l'heure répétée dure 4 h réelles (01:00+02 -> 04:00+01)
    night = spec(rules=(hours(1, 4),))
    n = [x for x in resolve(CHAIN, [night], PARIS, day) if x.schedule_id][1]
    assert span(n.start, n.end) == timedelta(hours=4)


def test_spring_forward_no_gap() -> None:
    day = date(2026, 3, 28)
    s = spec(rules=(hours(1, 5),))
    slots = resolve(CHAIN, [s], PARIS, day)
    assert_contiguous(slots, day)
    n = [x for x in slots if x.schedule_id][1]
    assert span(n.start, n.end) == timedelta(hours=3)


def test_two_rules_around_midnight_make_one_continuous_slot() -> None:
    day = date(2026, 10, 23)
    s = spec(rules=(hours(22, 24), hours(0, 6)))
    slots = resolve(CHAIN, [s], PARIS, day)
    assert_contiguous(slots, day)
    nights = [x for x in slots if x.schedule_id]
    # 23 00:00-06:00 | 23 22:00 -> 24 06:00 (fusionné) | 24 22:00 -> 25 06:00 (fusionné)
    assert nights[1].start.isoformat() == "2026-10-23T22:00:00+02:00"
    assert nights[1].end.isoformat() == "2026-10-24T06:00:00+02:00"


def test_overlapping_rules_of_one_schedule_do_not_duplicate() -> None:
    day = date(2026, 10, 23)
    s = spec(rules=(hours(8, 12), hours(10, 14)))
    slots = resolve(CHAIN, [s], PARIS, day)
    assert_contiguous(slots, day)
    first = [x for x in slots if x.schedule_id][0]
    assert (first.start.hour, first.end.hour) == (8, 14)


def test_rule_inside_nonexistent_spring_hour_is_contiguous() -> None:
    day = date(2026, 3, 29)  # 02:00 -> 03:00 n'existe pas
    s = spec(rules=(hours(2, 3),))
    assert_contiguous(resolve(CHAIN, [s], PARIS, day), day)


def test_override_next_day_changes_slots_only_on_that_day() -> None:
    day = date(2026, 10, 23)
    base = spec("site", rules=(hours(0, 24),))
    over = spec("screen", rules=(hours(8, 9),))
    slots = resolve(CHAIN, [base, over], PARIS, day)
    assert_contiguous(slots, day)
    assert [x.schedule_id for x in slots].count(over.id) == 2  # J et J+1
