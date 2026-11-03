"""Composition pure d'un créneau (mise en page, forçage de zone) : aucune base, aucune horloge."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from vds_server.diffusion.layout_compose import (
    EMPTY_PLAYLIST_KEY,
    ActiveZoneOverride,
    LayoutSpec,
    PlaylistVariant,
    ResolvedDefaults,
    ZoneSpec,
    compose_play_content,
    project_items,
)

START = datetime(2026, 6, 10, 8, tzinfo=UTC)
END = START + timedelta(hours=4)
PL_A, PL_B, PL_C = (UUID(int=i) for i in (1, 2, 3))
LAYOUT_ID = UUID(int=99)


def zone(key: str, **kw: object) -> ZoneSpec:
    base = {"x": 0, "y": 0, "w": 10000, "h": 10000, "playlist_id": PL_A}
    return ZoneSpec(key=key, **{**base, **kw})  # type: ignore[arg-type]


def layout(*zones: ZoneSpec, bg: str | None = None, media: UUID | None = None) -> LayoutSpec:
    return LayoutSpec(LAYOUT_ID, 3, "portrait", bg, media, zones)


def zo(n: int, keys: set[str], pl: UUID, a: datetime, b: datetime | None, prio: int = 0):  # type: ignore[no-untyped-def]
    return ActiveZoneOverride(
        UUID(int=1000 + n), frozenset(keys), pl, a, b, (prio, float(n), str(n))
    )


def compose(spec: LayoutSpec | None, **kw):  # type: ignore[no-untyped-def]
    return compose_play_content(
        playlist_id=None if spec else PL_A, layout=spec, slot_start=START, slot_end=END, **kw
    )


def test_playlist_target_is_the_1_8_document() -> None:
    r = compose(None)
    assert r.layout == {
        "zones": [{"id": "main", "x": 0, "y": 0, "w": 1, "h": 1, "playlist": f"pl_{PL_A}"}]
    }
    assert r.variants == {PlaylistVariant(PL_A)} and not r.missing_zone_override_ids


def test_zones_sorted_by_z_then_key_and_fractions() -> None:
    r = compose(
        layout(
            zone("b", z=1, x=1000, w=2500),
            zone("a", z=1),
            zone("top", z=9),
            zone("bottom", z=0),
        )
    )
    assert [z["id"] for z in r.layout["zones"]] == ["bottom", "a", "b", "top"]
    b = r.layout["zones"][2]
    assert (b["x"], b["w"], b["fit"], b["z"]) == (0.1, 0.25, "contain", 1)
    assert r.layout["id"] == str(LAYOUT_ID) and r.layout["revision"] == 3
    assert r.layout["orientation"] == "portrait" and "background" not in r.layout


def test_background_and_empty_zone() -> None:
    mid = UUID(int=7)
    r = compose(layout(zone("x", playlist_id=None), bg="#112233", media=mid))
    assert r.layout["zones"][0]["playlist"] == EMPTY_PLAYLIST_KEY and not r.variants
    assert r.layout["background"] == {"color": "#112233"} and r.background_media_id == mid
    only_media = compose(layout(zone("x"), media=mid))
    assert only_media.layout["background"] == {}


def test_zone_outside_window_is_omitted() -> None:
    expired = zone("old", active_until=START)  # <= début
    future = zone("late", active_from=END)  # >= fin
    ok = zone("ok")
    r = compose(layout(expired, future, ok))
    assert [z["id"] for z in r.layout["zones"]] == ["ok"]
    assert [
        z["id"]
        for z in compose(layout(zone("e", active_until=START + timedelta(seconds=1)))).layout[
            "zones"
        ]
    ] == ["e"]


def test_from_until_emitted_only_when_cutting_and_projected() -> None:
    cut = START + timedelta(hours=1)
    start_in = START + timedelta(hours=2)
    r = compose(layout(zone("z", active_from=start_in, active_until=cut + timedelta(hours=2))))
    z = r.layout["zones"][0]
    assert z["from"] == "2026-06-10T10:00:00Z" and z["until"] == "2026-06-10T11:00:00Z"
    v = PlaylistVariant(PL_A, 10_000, cut + timedelta(hours=2))
    assert z["playlist"] == v.key and v in r.variants
    beyond = compose(
        layout(
            zone("z", active_from=START - timedelta(hours=1), active_until=END + timedelta(hours=1))
        )
    )
    assert "from" not in beyond.layout["zones"][0] and "until" not in beyond.layout["zones"][0]
    assert beyond.layout["zones"][0]["playlist"] == PlaylistVariant(PL_A, 10_000, None).key


def test_default_duration_zone_then_resolved_default() -> None:
    r = compose(layout(zone("a", default_item_duration_ms=3000), zone("b")),
                defaults=ResolvedDefaults(default_item_duration_ms=7000))  # fmt: skip
    assert {v.fill_ms for v in r.variants} == {3000, 7000}


def test_playlist_expiry_projected_or_empty() -> None:
    inside = START + timedelta(hours=1)
    r = compose(layout(zone("a", playlist_expires_at=inside), zone("b", playlist_expires_at=START)))
    by = {z["id"]: z for z in r.layout["zones"]}
    assert by["b"]["playlist"] == EMPTY_PLAYLIST_KEY  # expirée : zone transparente
    assert by["a"]["playlist"] == PlaylistVariant(PL_A, 10_000, inside).key
    assert "until" not in by["a"]  # l'expiration de playlist ne retire pas la zone


def test_project_items_min_and_fill() -> None:
    items: list[dict[str, Any]] = [
        {"id": "1", "duration_ms": None, "until": None},
        {"id": "2", "duration_ms": 500, "until": "2026-06-10T09:00:00Z"},
        {"id": "3", "duration_ms": None, "until": "2026-06-10T12:00:00Z"},
    ]
    until = datetime(2026, 6, 10, 10, tzinfo=UTC)
    out = project_items(items, 4000, until)
    assert [(i["duration_ms"], i["until"]) for i in out] == [
        (4000, "2026-06-10T10:00:00Z"),
        (500, "2026-06-10T09:00:00Z"),
        (4000, "2026-06-10T10:00:00Z"),
    ]
    assert items[0]["duration_ms"] is None  # entrée non mutée


def test_zone_override_window_clamped_to_slot_and_zone() -> None:
    o = zo(1, {"banner"}, PL_B, START - timedelta(hours=1), END + timedelta(hours=1))
    z_from = START + timedelta(hours=1)
    r = compose(layout(zone("banner", active_from=z_from), zone("main")), zone_overrides=[o])
    banner = next(z for z in r.layout["zones"] if z["id"] == "banner")
    (w,) = banner["overrides"]
    assert w["from"] == "2026-06-10T09:00:00Z" and w["until"] == "2026-06-10T12:00:00Z"
    assert w["override_id"] == str(o.override_id) and w["playlist"].startswith(f"pl_{PL_B}")
    main = next(z for z in r.layout["zones"] if z["id"] == "main")
    assert "overrides" not in main  # autres zones intactes, créneau non découpé


def test_zone_override_frieze_has_no_overlap_and_follows_priority() -> None:
    low = zo(1, {"z"}, PL_B, START, START + timedelta(hours=3), prio=0)
    high = zo(2, {"z"}, PL_C, START + timedelta(hours=1), START + timedelta(hours=2), prio=5)
    r = compose(layout(zone("z")), zone_overrides=[low, high])
    wins = r.layout["zones"][0]["overrides"]
    assert [(w["override_id"], w["from"][11:16], w["until"][11:16]) for w in wins] == [
        (str(low.override_id), "08:00", "09:00"),
        (str(high.override_id), "09:00", "10:00"),
        (str(low.override_id), "10:00", "11:00"),
    ]
    assert all(a["until"] <= b["from"] for a, b in zip(wins, wins[1:], strict=False))


def test_same_priority_most_recent_wins() -> None:
    old, new = zo(1, {"z"}, PL_B, START, END), zo(2, {"z"}, PL_C, START, END)
    (w,) = compose(layout(zone("z")), zone_overrides=[old, new]).layout["zones"][0]["overrides"]
    assert w["override_id"] == str(new.override_id)


def test_zone_override_on_implicit_main() -> None:
    o = zo(1, {"main"}, PL_B, START + timedelta(hours=1), START + timedelta(hours=2))
    r = compose(None, zone_overrides=[o])
    (w,) = r.layout["zones"][0]["overrides"]
    assert w["playlist"] == f"pl_{PL_B}" and PlaylistVariant(PL_B) in r.variants
    assert not r.missing_zone_override_ids


def test_missing_zone_is_ignored_and_reported() -> None:
    o = zo(1, {"bandeau"}, PL_B, START, END)
    r = compose(layout(zone("main")), zone_overrides=[o])
    assert "overrides" not in r.layout["zones"][0] and not any(
        v.playlist_id == PL_B for v in r.variants
    )
    assert r.missing_zone_override_ids == {o.override_id}
    partial = zo(2, {"main", "bandeau"}, PL_B, START, END)
    r2 = compose(layout(zone("main")), zone_overrides=[partial])
    assert r2.layout["zones"][0]["overrides"] and r2.missing_zone_override_ids == {
        partial.override_id
    }
    elsewhere = zo(3, {"bandeau"}, PL_B, END + timedelta(hours=1), None)
    assert not compose(layout(zone("main")), zone_overrides=[elsewhere]).missing_zone_override_ids


def test_zone_override_outside_slot_has_no_effect() -> None:
    before = zo(1, {"z"}, PL_B, START - timedelta(hours=3), START)
    after = zo(2, {"z"}, PL_B, END, END + timedelta(hours=1))
    r = compose(layout(zone("z")), zone_overrides=[before, after])
    assert "overrides" not in r.layout["zones"][0]


def test_override_on_omitted_zone_is_ignored_without_missing() -> None:
    o = zo(1, {"gone"}, PL_B, START, END)
    r = compose(layout(zone("gone", active_until=START), zone("main")), zone_overrides=[o])
    assert [z["id"] for z in r.layout["zones"]] == ["main"]
    assert not r.missing_zone_override_ids  # la zone existe, elle est seulement expirée


def test_zone_override_spanning_past_slot_end_is_clamped_to_until() -> None:
    o = zo(1, {"z"}, PL_B, START + timedelta(hours=2), END + timedelta(days=1))
    r = compose(layout(zone("z")), zone_overrides=[o])
    (w,) = r.layout["zones"][0]["overrides"]
    assert w["from"] == "2026-06-10T10:00:00Z" and w["until"] == "2026-06-10T12:00:00Z"
