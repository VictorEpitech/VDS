"""Résolveur pur : cible mise en page (programmation et forçage d'écran), sans régression 1.8."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from vds_server.diffusion.resolver import OverrideSpec, ScheduleSpec, resolve
from vds_server.power.resolver import PowerSlot, apply_overrides

PARIS = ZoneInfo("Europe/Paris")
DAY = date(2026, 6, 10)
SCREEN = UUID(int=5)
CHAIN = {"organization": UUID(int=1), "site": UUID(int=2), "screen": SCREEN}
PL, LAY, LAY2 = UUID(int=11), UUID(int=12), UUID(int=13)
T0 = datetime(2026, 6, 1, tzinfo=UTC)


def sched(
    n: int, *, pl: UUID | None = None, lay: UUID | None = None, prio: int = 0
) -> ScheduleSpec:
    return ScheduleSpec(
        id=UUID(int=100 + n), playlist_id=pl, target_type="screen", target_id=SCREEN,
        priority=prio, updated_at=T0, layout_id=lay,
    )  # fmt: skip


def force(
    n: int, a: datetime, b: datetime, *, pl: UUID | None = None, lay: UUID | None = None
) -> OverrideSpec:
    return OverrideSpec(
        id=UUID(int=200 + n), target_type="screen", target_id=SCREEN, starts_at=a, ends_at=b,
        playlist_id=pl, priority=0, created_at=T0, layout_id=lay,
    )  # fmt: skip


def test_layout_schedule_slot_carries_layout_only() -> None:
    (slot,) = resolve(CHAIN, [sched(1, lay=LAY)], PARIS, DAY)
    assert slot.layout_id == LAY and slot.playlist_id is None
    assert not slot.is_off and slot.source == "schedule"


def test_playlist_schedule_unchanged() -> None:
    (slot,) = resolve(CHAIN, [sched(1, pl=PL)], PARIS, DAY)
    assert slot.playlist_id == PL and slot.layout_id is None and not slot.is_off


def test_screen_override_on_layout_beats_schedule_and_is_not_off() -> None:
    a = datetime(2026, 6, 10, 8, tzinfo=UTC)
    slots = resolve(
        CHAIN, [sched(1, pl=PL)], PARIS, DAY, [force(1, a, a + timedelta(hours=1), lay=LAY)]
    )
    forced = [s for s in slots if s.override_id is not None]
    assert len(forced) == 1 and forced[0].layout_id == LAY and not forced[0].is_off
    assert forced[0].schedule_id is None and forced[0].source == "override"
    assert all(s.layout_id is None for s in slots if s.override_id is None)


def test_off_override_still_off_and_beats_layout_override() -> None:
    a = datetime(2026, 6, 10, 8, tzinfo=UTC)
    b = a + timedelta(hours=1)
    off = force(1, a, b)
    slots = resolve(CHAIN, [sched(1, lay=LAY)], PARIS, DAY, [off, force(2, a, b, lay=LAY2)])
    (hit,) = [s for s in slots if s.override_id is not None]
    assert hit.is_off and hit.override_id == off.id


def test_explain_reports_layout_in_refs_without_changing_slots() -> None:
    plain = resolve(CHAIN, [sched(1, lay=LAY), sched(2, pl=PL, prio=5)], PARIS, DAY)
    explained = resolve(
        CHAIN, [sched(1, lay=LAY), sched(2, pl=PL, prio=5)], PARIS, DAY, explain=True
    )
    assert [(s.start, s.end, s.schedule_id, s.layout_id) for s in plain] == [
        (s.start, s.end, s.schedule_id, s.layout_id) for s in explained
    ]
    (slot,) = explained
    assert slot.playlist_id == PL and [lo.ref.layout_id for lo in slot.losers] == [LAY]


def test_power_override_on_layout_turns_screen_on() -> None:
    a = datetime(2026, 6, 10, 8, tzinfo=UTC)
    b = a + timedelta(hours=1)
    base = [PowerSlot(a - timedelta(hours=2), b + timedelta(hours=2), "off")]
    lit = apply_overrides(base, CHAIN, [force(1, a, b, lay=LAY)], UTC)
    assert [s.state for s in lit] == ["off", "on", "off"]
    dark = apply_overrides(base, CHAIN, [force(2, a, b)], UTC)
    assert {s.state for s in dark} == {"off"}
