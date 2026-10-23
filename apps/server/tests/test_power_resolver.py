"""Résolution pure de l'alimentation : plan, calendriers, exceptions, DST, minuit."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from vds_server.power.resolver import (
    ExceptionSpec,
    PlanRule,
    PowerSlot,
    resolve,
    state_at,
    to_wire,
)

PARIS = ZoneInfo("Europe/Paris")
WEEK = 31  # lundi..vendredi
ALL = 127
WED = date(2026, 10, 21)  # mercredi ; jeudi 22 ensuite


def dur(s: PowerSlot) -> timedelta:
    return s.end.astimezone(UTC) - s.start.astimezone(UTC)


def hm(s: PowerSlot) -> tuple[str, str, str]:
    return (s.start.strftime("%d %H:%M"), s.end.strftime("%d %H:%M"), s.state)


def test_no_plan_is_always_on() -> None:
    slots = resolve(None, set(), [], PARIS, WED)
    assert [hm(s) for s in slots] == [("21 00:00", "23 00:00", "on")]


def test_plan_hours_two_days() -> None:
    slots = resolve([PlanRule(WEEK, 480, 1080)], set(), [], PARIS, WED)
    assert [hm(s) for s in slots] == [
        ("21 00:00", "21 08:00", "off"),
        ("21 08:00", "21 18:00", "on"),
        ("21 18:00", "22 08:00", "off"),
        ("22 08:00", "22 18:00", "on"),
        ("22 18:00", "23 00:00", "off"),
    ]


def test_weekend_off_and_weekday_mask() -> None:
    sat = date(2026, 10, 24)
    slots = resolve([PlanRule(WEEK, 480, 1080)], set(), [], PARIS, sat)
    assert [s.state for s in slots] == ["off"]  # samedi et dimanche


def test_midnight_crossing_merges() -> None:
    rules = [PlanRule(ALL, 1200, 1440), PlanRule(ALL, 0, 480)]
    slots = resolve(rules, set(), [], PARIS, WED)
    assert [hm(s) for s in slots] == [
        ("21 00:00", "21 08:00", "on"),
        ("21 08:00", "21 20:00", "off"),
        ("21 20:00", "22 08:00", "on"),
        ("22 08:00", "22 20:00", "off"),
        ("22 20:00", "23 00:00", "on"),
    ]


def test_dst_spring_forward_day_is_23h() -> None:
    day = date(2026, 3, 28)  # bascule le 29 à 02:00 -> 03:00
    slots = resolve([PlanRule(ALL, 0, 1440)], set(), [], PARIS, day)
    assert len(slots) == 1 and slots[0].state == "on"
    assert dur(slots[0]) == timedelta(hours=47)
    # 08:00 local avant/après bascule = décalages différents, mêmes heures murales
    slots = resolve([PlanRule(ALL, 480, 1080)], set(), [], PARIS, day)
    assert slots[1].start.isoformat().endswith("+01:00")
    assert slots[3].start.isoformat() == "2026-03-29T08:00:00+02:00"


def test_dst_fall_back_day_is_25h() -> None:
    day = date(2026, 10, 24)
    slots = resolve([PlanRule(ALL, 0, 1440)], set(), [], PARIS, day)
    assert dur(slots[0]) == timedelta(hours=49)
    slots = resolve([PlanRule(ALL, 480, 1080)], set(), [], PARIS, day)
    assert slots[3].start.isoformat() == "2026-10-25T08:00:00+01:00"
    assert dur(slots[2]) == timedelta(hours=14 + 1)  # 18:00 -> 08:00 + 1 h


def test_dst_nonexistent_wall_time_is_resolved() -> None:
    day = date(2026, 3, 29)
    slots = resolve([PlanRule(ALL, 150, 1440)], set(), [], PARIS, day)  # 02:30 n'existe pas
    assert slots[1].state == "on" and slots[1].start.astimezone(UTC).hour == 1


def test_calendar_day_turns_off_all_day() -> None:
    slots = resolve([PlanRule(WEEK, 480, 1080)], {WED}, [], PARIS, WED)
    assert [hm(s) for s in slots][:2] == [
        ("21 00:00", "22 08:00", "off"),
        ("22 08:00", "22 18:00", "on"),
    ]


def test_calendar_ignored_without_plan() -> None:
    assert [s.state for s in resolve(None, {WED}, [], PARIS, WED)] == ["on"]


def test_exception_beats_plan_and_calendar() -> None:
    plan = [PlanRule(WEEK, 480, 1080)]
    day_off = ExceptionSpec("screen", WED, WED, "off")
    slots = resolve(plan, set(), [day_off], PARIS, WED)
    assert [hm(s) for s in slots][0] == ("21 00:00", "22 08:00", "off")
    reopen = ExceptionSpec("screen", WED, WED, "on", 600, 720)
    slots = resolve(plan, {WED}, [reopen], PARIS, WED)
    assert [hm(s) for s in slots][:3] == [
        ("21 00:00", "21 10:00", "off"),
        ("21 10:00", "21 12:00", "on"),
        ("21 12:00", "22 08:00", "off"),
    ]


def test_exception_on_when_no_plan_can_be_off() -> None:
    e = ExceptionSpec("site", WED, WED, "off", 720, 840)
    slots = resolve(None, set(), [e], PARIS, WED)
    assert [hm(s) for s in slots][:3] == [
        ("21 00:00", "21 12:00", "on"),
        ("21 12:00", "21 14:00", "off"),
        ("21 14:00", "23 00:00", "on"),
    ]


def test_most_specific_level_wins_then_off_wins_on_tie() -> None:
    site_off = ExceptionSpec("site", WED, WED, "off")
    screen_on = ExceptionSpec("screen", WED, WED, "on", 600, 720)
    slots = resolve(None, set(), [site_off, screen_on], PARIS, WED)
    assert [hm(s) for s in slots][:3] == [
        ("21 00:00", "21 10:00", "off"),
        ("21 10:00", "21 12:00", "on"),
        ("21 12:00", "22 00:00", "off"),
    ]
    tie = [ExceptionSpec("group", WED, WED, "on"), ExceptionSpec("group", WED, WED, "off")]
    assert {s.state for s in resolve(None, set(), tie, PARIS, WED)[:1]} == {"off"}


def test_multi_day_exception_range_and_out_of_window() -> None:
    e = ExceptionSpec("organization", date(2026, 10, 22), date(2026, 10, 30), "off")
    slots = resolve(None, set(), [e], PARIS, WED)
    assert [hm(s) for s in slots] == [
        ("21 00:00", "22 00:00", "on"),
        ("22 00:00", "23 00:00", "off"),
    ]
    far = ExceptionSpec("organization", date(2026, 12, 1), date(2026, 12, 2), "off")
    assert [s.state for s in resolve(None, set(), [far], PARIS, WED)] == ["on"]


def test_wire_and_state_at() -> None:
    slots = resolve([PlanRule(WEEK, 480, 1080)], set(), [], PARIS, WED)
    wire = to_wire(slots)
    assert wire[1] == {
        "from": "2026-10-21T08:00:00+02:00",
        "to": "2026-10-21T18:00:00+02:00",
        "state": "on",
    }
    assert state_at(slots, datetime(2026, 10, 21, 7, 0, tzinfo=UTC)) == "on"  # 09:00 local
    assert state_at(slots, datetime(2026, 10, 21, 17, 0, tzinfo=UTC)) == "off"
