"""Jeu d'or du résolveur : sorties exactes figées avant l'extraction de `resolve()` (P0-1)."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from vds_server.diffusion.resolver import OverrideSpec, Rule, ScheduleSpec, Slot, resolve

PARIS = ZoneInfo("Europe/Paris")
ORG, SITE, GROUP, SCREEN = (UUID(int=i) for i in (1, 2, 3, 4))
CHAIN = {"organization": ORG, "site": SITE, "group": GROUP, "screen": SCREEN}
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def sid(n: int) -> UUID:
    return UUID(int=1000 + n)


def sch(
    n: int, level: str = "screen", *, priority: int = 0, updated: int = 0, **kw: object
) -> ScheduleSpec:
    return ScheduleSpec(
        id=sid(n),
        playlist_id=UUID(int=2000 + n),
        target_type=level,
        target_id=CHAIN[level],
        priority=priority,
        updated_at=T0 + timedelta(days=updated),
        **kw,  # type: ignore[arg-type]
    )


def ovr(
    n: int, start: datetime, end: datetime, *, playlist: bool = True, **kw: object
) -> OverrideSpec:
    return OverrideSpec(
        id=UUID(int=3000 + n),
        target_type=str(kw.pop("level", "screen")),
        target_id=CHAIN[str(kw.get("lvl", "screen"))],
        starts_at=start,
        ends_at=end,
        playlist_id=UUID(int=4000 + n) if playlist else None,
        priority=int(kw.pop("priority", 0)),  # type: ignore[call-overload]
        created_at=T0,
        respect_scheduled_off=bool(kw.pop("respect", False)),
    )


def flat(slots: list[Slot]) -> list[tuple[str, str, int | None, int | None]]:
    """(début, fin, n° programmation, n° forçage) : 1000+n et 3000+n ramenés à n."""
    return [
        (
            s.start.isoformat(),
            s.end.isoformat(),
            None if s.schedule_id is None else s.schedule_id.int - 1000,
            None if s.override_id is None else s.override_id.int - 3000,
        )
        for s in slots
    ]


def h(a: int, b: int, wd: int = 127) -> Rule:
    return Rule(wd, a * 60, b * 60)


def test_gold_overlap_priority_then_specificity() -> None:
    specs = [
        sch(1, "organization", rules=(h(0, 24),)),
        sch(2, "site", priority=10, rules=(h(8, 18),)),
        sch(3, "screen", priority=-5, rules=(h(12, 14),)),
        sch(4, "screen", priority=5, rules=(h(13, 16),)),
        sch(5, "group", priority=500, rules=(h(17, 20),)),
    ]
    got = flat(resolve(CHAIN, specs, PARIS, date(2026, 6, 10)))
    assert got[:7] == [
        ("2026-06-10T00:00:00+02:00", "2026-06-10T08:00:00+02:00", 1, None),
        ("2026-06-10T08:00:00+02:00", "2026-06-10T12:00:00+02:00", 2, None),
        ("2026-06-10T12:00:00+02:00", "2026-06-10T13:00:00+02:00", 3, None),
        ("2026-06-10T13:00:00+02:00", "2026-06-10T16:00:00+02:00", 4, None),
        ("2026-06-10T16:00:00+02:00", "2026-06-10T17:00:00+02:00", 2, None),
        ("2026-06-10T17:00:00+02:00", "2026-06-10T20:00:00+02:00", 5, None),
        ("2026-06-10T20:00:00+02:00", "2026-06-11T08:00:00+02:00", 1, None),  # fusion minuit
    ]
    assert len(got) == 13


def test_gold_tie_breaks_on_updated_then_id() -> None:
    a = sch(1, rules=(h(8, 10),), updated=1)
    b = sch(2, rules=(h(8, 10),), updated=2)
    got = flat(resolve(CHAIN, [a, b], PARIS, date(2026, 6, 10)))
    assert got[1][2] == 2
    c = sch(3, rules=(h(8, 10),), updated=2)  # même date : l'id le plus grand gagne
    got = flat(resolve(CHAIN, [b, c], PARIS, date(2026, 6, 10)))
    assert got[1][2] == 3


def test_gold_period_and_weekdays() -> None:
    # mercredi 10 juin 2026 : starts_on exclut J, ends_on exclut J+1
    s = sch(1, rules=(h(8, 10, 1 << 2),), starts_on=date(2026, 6, 10), ends_on=date(2026, 6, 10))
    assert flat(resolve(CHAIN, [s], PARIS, date(2026, 6, 10))) == [
        ("2026-06-10T00:00:00+02:00", "2026-06-10T08:00:00+02:00", None, None),
        ("2026-06-10T08:00:00+02:00", "2026-06-10T10:00:00+02:00", 1, None),
        ("2026-06-10T10:00:00+02:00", "2026-06-12T00:00:00+02:00", None, None),
    ]


def test_gold_disabled_and_foreign_target_ignored() -> None:
    other = ScheduleSpec(sid(2), UUID(int=1), "screen", UUID(int=99), 0, T0)
    off = sch(1, enabled=False)
    assert flat(resolve(CHAIN, [other, off], PARIS, date(2026, 6, 10))) == [
        ("2026-06-10T00:00:00+02:00", "2026-06-12T00:00:00+02:00", None, None)
    ]


def test_gold_override_beats_specificity_and_cuts_midnight() -> None:
    specs = [sch(1, "screen", rules=(h(0, 24),)), sch(2, "organization")]
    o = ovr(
        1,
        datetime(2026, 6, 10, 21, 0, tzinfo=UTC),
        datetime(2026, 6, 10, 23, 0, tzinfo=UTC),
        level="organization",
        lvl="organization",
    )
    assert flat(resolve(CHAIN, specs, PARIS, date(2026, 6, 10), [o])) == [
        ("2026-06-10T00:00:00+02:00", "2026-06-10T23:00:00+02:00", 1, None),
        ("2026-06-10T23:00:00+02:00", "2026-06-11T01:00:00+02:00", None, 1),
        ("2026-06-11T01:00:00+02:00", "2026-06-12T00:00:00+02:00", 1, None),
    ]


def test_gold_override_off_and_respect_scheduled_off() -> None:
    s = sch(1, rules=(h(8, 10),))
    day = date(2026, 6, 10)
    a = datetime(2026, 6, 10, 5, 0, tzinfo=UTC)  # 07:00 locale
    b = datetime(2026, 6, 10, 7, 0, tzinfo=UTC)  # 09:00 locale
    plain = ovr(1, a, b, playlist=False)
    assert flat(resolve(CHAIN, [s], PARIS, day, [plain]))[1:3] == [
        ("2026-06-10T07:00:00+02:00", "2026-06-10T09:00:00+02:00", None, 1),
        ("2026-06-10T09:00:00+02:00", "2026-06-10T10:00:00+02:00", 1, None),
    ]
    respect = ovr(1, a, b, respect=True)
    got = flat(resolve(CHAIN, [s], PARIS, day, [respect]))
    assert got[0] == ("2026-06-10T00:00:00+02:00", "2026-06-10T08:00:00+02:00", None, None)
    assert got[1] == ("2026-06-10T08:00:00+02:00", "2026-06-10T09:00:00+02:00", None, 1)


def test_gold_override_priority_and_off_first() -> None:
    a = datetime(2026, 6, 10, 5, 0, tzinfo=UTC)
    b = datetime(2026, 6, 10, 7, 0, tzinfo=UTC)
    low, high, off = ovr(1, a, b), ovr(2, a, b, priority=9), ovr(3, a, b, playlist=False)
    day = date(2026, 6, 10)
    assert flat(resolve(CHAIN, [], PARIS, day, [low, high]))[1][3] == 2
    assert flat(resolve(CHAIN, [], PARIS, day, [low, high, off]))[1][3] == 3


def test_gold_expiry_mid_slot() -> None:
    until = datetime(2026, 6, 10, 9, 30, tzinfo=UTC)  # 11:30 locale
    s = sch(1, rules=(h(10, 14),), until=until)
    t = sch(2, "organization", rules=(h(0, 24),))
    got = flat(resolve(CHAIN, [s, t], PARIS, date(2026, 6, 10)))
    assert got[:3] == [
        ("2026-06-10T00:00:00+02:00", "2026-06-10T10:00:00+02:00", 2, None),
        ("2026-06-10T10:00:00+02:00", "2026-06-10T11:30:00+02:00", 1, None),
        ("2026-06-10T11:30:00+02:00", "2026-06-12T00:00:00+02:00", 2, None),
    ]


def test_gold_midnight_end_and_two_rules() -> None:
    s = sch(1, rules=(h(22, 24), h(0, 2)))
    assert flat(resolve(CHAIN, [s], PARIS, date(2026, 6, 10))) == [
        ("2026-06-10T00:00:00+02:00", "2026-06-10T02:00:00+02:00", 1, None),
        ("2026-06-10T02:00:00+02:00", "2026-06-10T22:00:00+02:00", None, None),
        ("2026-06-10T22:00:00+02:00", "2026-06-11T02:00:00+02:00", 1, None),
        ("2026-06-11T02:00:00+02:00", "2026-06-11T22:00:00+02:00", None, None),
        ("2026-06-11T22:00:00+02:00", "2026-06-12T00:00:00+02:00", 1, None),
    ]


def test_gold_dst_end_october_and_march() -> None:
    s = sch(1, rules=(h(0, 24),))
    fall = resolve(CHAIN, [s], PARIS, date(2026, 10, 25))  # 25 h
    assert flat(fall) == [("2026-10-25T00:00:00+02:00", "2026-10-27T00:00:00+01:00", 1, None)]
    assert (fall[0].end.astimezone(UTC) - fall[0].start.astimezone(UTC)) == timedelta(hours=49)
    spring = resolve(CHAIN, [s], PARIS, date(2026, 3, 29))  # 23 h
    assert (spring[0].end.astimezone(UTC) - spring[0].start.astimezone(UTC)) == timedelta(hours=47)
    # règle 01:00-04:00 le jour du passage à l'heure d'été : 2 h réelles
    r = sch(2, rules=(h(1, 4),))
    got = resolve(CHAIN, [r], PARIS, date(2026, 3, 29))
    play = next(x for x in got if x.schedule_id)
    assert play.end.astimezone(UTC) - play.start.astimezone(UTC) == timedelta(hours=2)
    # 02:30 n'existe pas ce jour-là : ne casse pas la contiguïté
    z = sch(3, rules=(Rule(127, 150, 210),))
    zs = resolve(CHAIN, [z], PARIS, date(2026, 3, 29))
    assert all(a.end == b.start for a, b in zip(zs, zs[1:], strict=False))
