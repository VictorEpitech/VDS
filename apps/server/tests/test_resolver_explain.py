"""Mode explain : raisons, perdants, conflits, marqueurs ; explain=False ne change rien."""

from datetime import UTC, date, datetime
from uuid import UUID

from test_resolver_gold import CHAIN, PARIS, h, ovr, sch
from vds_server.diffusion.explain import conflicts, markers
from vds_server.diffusion.resolver import ResolveInput, Slot, resolve, resolve_input

DAY = date(2026, 6, 10)


def inp(specs: list, ovs: list | None = None) -> ResolveInput:  # type: ignore[type-arg]
    return ResolveInput(CHAIN, specs, ovs or [], PARIS, DAY)


def plain(slots: list[Slot]) -> list[tuple[object, ...]]:
    """Créneaux fusionnés sans explication (explain ajoute des coupes : on refusionne)."""
    out: list[tuple[object, ...]] = []
    for s in slots:
        key = (s.schedule_id, s.playlist_id, s.override_id)
        if out and out[-1][2:] == key:
            out[-1] = (out[-1][0], s.end, *key)
        else:
            out.append((s.start, s.end, *key))
    return out


def reasons(slots: list[Slot]) -> list[str | None]:
    return [s.reason for s in slots]


def test_explain_false_is_identical_and_bare() -> None:
    specs = [sch(1, "site", rules=(h(8, 18),)), sch(2, "screen", rules=(h(12, 14),))]
    base = resolve(CHAIN, specs, PARIS, DAY)
    assert all(s.reason is None and s.losers == () for s in base)
    assert resolve_input(inp(specs)) == base
    assert plain(resolve(CHAIN, specs, PARIS, DAY, explain=True)) == plain(base)


def test_reason_only_candidate_and_no_candidate() -> None:
    sl = resolve(CHAIN, [sch(1, rules=(h(8, 10),))], PARIS, DAY, explain=True)
    assert sl[0].reason == "no_candidate" and sl[1].reason == "only_candidate"


def test_reason_more_specific_and_higher_priority_and_tie() -> None:
    screen, site = (
        sch(1, "screen", rules=(h(8, 9),)),
        sch(2, "site", priority=900, rules=(h(8, 9),)),
    )
    s = next(x for x in resolve(CHAIN, [screen, site], PARIS, DAY, explain=True) if x.schedule_id)
    assert s.reason == "more_specific" and s.losers[0].reason == "more_specific"
    hi, lo = sch(3, priority=5, rules=(h(8, 9),)), sch(4, priority=1, rules=(h(8, 9),))
    s = next(x for x in resolve(CHAIN, [hi, lo], PARIS, DAY, explain=True) if x.schedule_id)
    assert s.reason == "higher_priority" and s.losers[0].ref.id == lo.id
    a, b = sch(5, rules=(h(8, 9),), updated=1), sch(6, rules=(h(8, 9),), updated=2)
    s = next(x for x in resolve(CHAIN, [a, b], PARIS, DAY, explain=True) if x.schedule_id)
    assert s.schedule_id == b.id and s.reason == "tie_break"


def test_loser_reasons_disabled_period_rules_expired() -> None:
    win = sch(1, rules=(h(8, 20),))
    dis = sch(2, enabled=False)
    per = sch(3, rules=(h(0, 24),), starts_on=date(2026, 6, 11))
    wk = sch(4, rules=(h(0, 24, 1 << 6),))  # dimanche seulement
    exp = sch(5, "organization", rules=(h(0, 24),), until=datetime(2026, 6, 10, 9, 0, tzinfo=UTC))
    sl = resolve(CHAIN, [win, dis, per, wk, exp], PARIS, DAY, explain=True)
    at_noon = next(s for s in sl if s.start.hour <= 12 < s.end.hour and s.start.day == 10)
    by_id = {x.ref.id: x.reason for x in at_noon.losers}
    assert by_id == {
        dis.id: "disabled",
        per.id: "outside_period",
        wk.id: "outside_rules",
        exp.id: "content_expired",
    }
    nxt = next(s for s in sl if s.start.day == 11 and s.start.hour == 8)
    assert nxt.schedule_id == per.id and nxt.reason == "tie_break"  # id le plus grand


def test_override_reasons() -> None:
    s = sch(1, rules=(h(8, 10),))
    a, b = datetime(2026, 6, 10, 5, tzinfo=UTC), datetime(2026, 6, 10, 7, tzinfo=UTC)
    hi, lo = ovr(1, a, b, priority=9), ovr(2, a, b)
    sl = resolve(CHAIN, [s], PARIS, DAY, [hi, lo], explain=True)
    seg = next(x for x in sl if x.override_id and x.start.hour == 8)
    assert seg.reason == "override_active"
    by = {x.ref.id: x.reason for x in seg.losers}
    assert by == {s.id: "override_active", lo.id: "higher_priority"}
    tie = resolve(CHAIN, [], PARIS, DAY, [ovr(3, a, b), ovr(4, a, b)], explain=True)
    assert next(x for x in tie if x.override_id).losers[0].reason == "tie_break"
    resp = ovr(5, a, b, respect=True)  # 07:00-08:00 locale : programmation hors plage
    sl = resolve(CHAIN, [s], PARIS, DAY, [resp], explain=True)
    off = next(x for x in sl if x.start.hour == 7)
    assert off.reason == "no_candidate"
    assert {x.ref.id: x.reason for x in off.losers}[resp.id] == "scheduled_off"


def test_conflict_never_plays_partial_and_tie() -> None:
    hidden = sch(1, "organization", rules=(h(9, 10),))
    cover = sch(2, "screen", rules=(h(8, 11),))
    part = sch(3, "site", rules=(h(10, 12),))
    cs = conflicts(
        inp([hidden, cover, part]), resolve(CHAIN, [hidden, cover, part], PARIS, DAY, explain=True)
    )
    kinds = {(c.kind, c.source.id) for c in cs}
    assert ("never_plays", hidden.id) in kinds and ("partially_masked", part.id) in kinds
    a, b = sch(4, rules=(h(8, 9),), updated=1), sch(5, rules=(h(8, 9),), updated=1)
    cs = conflicts(inp([a, b]), resolve(CHAIN, [a, b], PARIS, DAY, explain=True))
    tie = next(c for c in cs if c.kind == "ambiguous_tie")
    assert tie.severity == "warning" and tie.occurrences == 2 and tie.other is not None


def test_conflict_content_expires_empty_gap() -> None:
    until = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)  # 11:00 locale, au milieu de 10-14
    s = sch(1, rules=(h(10, 14),), until=until)
    sl = resolve(CHAIN, [s], PARIS, DAY, explain=True)
    cs = conflicts(inp([s]), sl)
    ce = next(c for c in cs if c.kind == "content_expires")
    assert ce.severity == "warning" and ce.first_at == datetime(2026, 6, 10, 11, tzinfo=PARIS)
    g = sch(2, rules=(h(8, 9), h(12, 13)))
    cs = conflicts(inp([g]), resolve(CHAIN, [g], PARIS, DAY, explain=True))
    gap = next(c for c in cs if c.kind == "gap")
    assert gap.first_at == datetime(2026, 6, 10, 9, tzinfo=PARIS) and gap.severity == "info"
    e = sch(3, rules=(h(8, 9),))
    assert e.playlist_id is not None
    cs = conflicts(
        inp([e]),
        resolve(CHAIN, [e], PARIS, DAY, explain=True),
        empty_playlists=frozenset({e.playlist_id}),
    )
    assert next(c for c in cs if c.kind == "content_empty").severity == "warning"


def test_markers() -> None:
    until = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)
    s = sch(1, rules=(h(10, 14),), until=until)
    o = ovr(1, datetime(2026, 6, 10, 5, tzinfo=UTC), datetime(2026, 6, 10, 6, tzinfo=UTC))
    i = inp([s], [o])
    sl = resolve(CHAIN, [s], PARIS, DAY, [o], explain=True)
    mk = markers(i, sl, media_until=[(UUID(int=77), datetime(2026, 6, 11, 8, tzinfo=UTC))])
    by = {m.kind: m for m in mk}
    assert by["playlist_expires"].impact == "gap" and by["playlist_expires"].ref_id == s.playlist_id
    assert by["override_ends"].impact == "gap"  # retour à une programmation hors plage
    assert by["media_expires"].impact == "none"
    assert {"schedule_starts", "schedule_ends"} <= set(by)
    assert mk == sorted(mk, key=lambda m: (m.at, m.kind, str(m.ref_id)))
