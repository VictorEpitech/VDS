"""Planning admin (timeline, at, simulate), aux deux VDS_BASE_PATH (fixture `p`)."""
# ruff: noqa: F811

import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import Engine, event, pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, _diffusion_clean, mk_playlist  # noqa: F401
from test_fleet_playback import screen_of, site_of

TODAY = date.today()
FROM = (TODAY + timedelta(days=1)).isoformat()
TO = (TODAY + timedelta(days=3)).isoformat()


async def sched(c: AsyncClient, p: str, pl: str, tt: str, tid: str, name: str, **kw: Any) -> str:
    body = {"name": name, "playlist_id": pl, "target_type": tt, "target_id": tid, **kw}
    r = await c.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def tl(c: AsyncClient, p: str, tt: str, tid: str, **q: Any) -> Any:
    params = {"target_type": tt, "target_id": tid, "from": FROM, "to": TO, **q}
    return await c.get(f"{p}/api/v1/planning/timeline", params=params)


async def world(c: AsyncClient, p: str) -> dict[str, str]:
    site = await site_of(c, p, "a")
    grp = (
        await c.post(f"{p}/api/v1/sites/{site}/groups", json={"name": "G"}, headers=SAME)
    ).json()["id"]
    return {
        "site": site,
        "group": grp,
        "scr": await screen_of(c, p, site, "Hall", grp),
        "pl_site": await mk_playlist(c, p, "PlSite"),
        "pl_scr": await mk_playlist(c, p, "PlEcran"),
    }


async def test_screen_timeline_explained(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    s_site = await sched(admin, p, w["pl_site"], "site", w["site"], "Site", priority=100)
    s_scr = await sched(
        admin, p, w["pl_scr"], "screen", w["scr"], "Ecran",
        rules=[{"weekdays": 127, "start": "08:00", "end": "12:00"}],
    )  # fmt: skip
    r = await tl(admin, p, "screen", w["scr"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == 1 and body["mixed_timezones"] is False and body["next_cursor"] is None
    (row,) = body["screens"]
    assert row["screen_name"] == "Hall" and row["timezone"] == "Europe/Paris"
    wins = [s for s in row["segments"] if s["source"]["id"] == s_scr]
    assert wins and wins[0]["reason"] == "more_specific" and wins[0]["state"] == "play"
    assert wins[0]["source"]["inherited"] is False and wins[0]["content"]["name"] == "PlEcran"
    assert wins[0]["losers"][0]["source"]["id"] == s_site
    assert wins[0]["losers"][0]["reason"] == "more_specific"
    inherited = [s for s in row["segments"] if s["source"]["id"] == s_site]
    assert inherited and inherited[0]["source"]["inherited"] is True
    assert inherited[0]["source"]["priority_level"] == "high"
    assert inherited[0]["source"]["target_name"] == "a"
    # voies : les deux programmations, l'une et l'autre avec une part gagnante
    lanes = {x["source"]["id"]: x for x in body["lanes"]}
    assert set(lanes) == {s_site, s_scr}
    assert 0 < lanes[s_site]["effective_ratio"] < 1 and lanes[s_scr]["effective_ratio"] == 1.0
    assert any(c["kind"] == "partially_masked" for c in row["conflicts"])
    assert any(c["kind"] == "content_empty" for c in row["conflicts"])  # playlists sans élément
    assert row["markers"] and {m["kind"] for m in row["markers"]} >= {"schedule_starts"}


async def test_group_lines_not_explained_by_default(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    await screen_of(admin, p, w["site"], "Autre", w["group"])
    await sched(admin, p, w["pl_site"], "group", w["group"], "Groupe")
    body = (await tl(admin, p, "group", w["group"])).json()
    assert [r["screen_name"] for r in body["screens"]] == ["Autre", "Hall"]
    for row in body["screens"]:
        assert row["conflicts"] == []
        assert all(s["losers"] == [] and s["reason"] is None for s in row["segments"])
    forced = (await tl(admin, p, "group", w["group"], explain="true")).json()
    assert forced["screens"][0]["segments"][0]["reason"] == "only_candidate"


async def test_bounds_and_errors(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    far = (TODAY + timedelta(days=43)).isoformat()
    r = await tl(admin, p, "screen", w["scr"], to=far)
    assert r.status_code == 422 and r.json()["code"] == "planning.window_too_large"
    ok = (TODAY + timedelta(days=42)).isoformat()
    assert (await tl(admin, p, "screen", w["scr"], to=ok)).status_code == 200  # 42 jours inclus
    r = await tl(admin, p, "screen", w["scr"], to=(TODAY - timedelta(days=1)).isoformat())
    assert r.json()["code"] == "planning.window_invalid"
    assert (await tl(admin, p, "screen", w["scr"], limit=101)).status_code == 422
    assert (await tl(admin, p, "screen", w["scr"], cursor="%%%")).json()["code"] == (
        "planning.cursor_invalid"
    )
    missing = "00000000-0000-4000-8000-000000000001"
    r = await tl(admin, p, "group", missing)
    assert r.status_code == 404 and r.json()["code"] == "planning.target_not_found"


async def test_pagination(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    for n in ("B", "C", "D"):
        await screen_of(admin, p, w["site"], n)
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        q: dict[str, Any] = {"limit": 3}
        if cursor:
            q["cursor"] = cursor
        body = (await tl(admin, p, "site", w["site"], **q)).json()
        seen += [r["screen_name"] for r in body["screens"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == ["B", "C", "D", "Hall"] and pages == 2


async def test_etag_and_304(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    sid = await sched(admin, p, w["pl_site"], "site", w["site"], "Site")
    r1 = await tl(admin, p, "screen", w["scr"])
    etag = r1.headers["etag"]
    r2 = await admin.get(
        f"{p}/api/v1/planning/timeline",
        params={"target_type": "screen", "target_id": w["scr"], "from": FROM, "to": TO},
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == etag
    # une modification des entrées change l'ETag
    await admin.patch(f"{p}/api/v1/schedules/{sid}", json={"priority": 5}, headers=SAME)
    r3 = await admin.get(
        f"{p}/api/v1/planning/timeline",
        params={"target_type": "screen", "target_id": w["scr"], "from": FROM, "to": TO},
        headers={"If-None-Match": etag},
    )
    assert r3.status_code == 200 and r3.headers["etag"] != etag


async def test_permissions(
    anon: AsyncClient, admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    w = await world(admin, p)
    q = {"target_type": "screen", "target_id": w["scr"], "from": FROM, "to": TO}
    assert (await editor.get(f"{p}/api/v1/planning/timeline", params=q)).status_code == 200
    anon.cookies.clear()
    assert (await anon.get(f"{p}/api/v1/planning/timeline", params=q)).status_code == 401
    assert (await anon.get(f"{p}/api/v1/planning/at", params=q)).status_code == 401


async def test_at(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    sid = await sched(
        admin, p, w["pl_site"], "site", w["site"], "Site",
        rules=[{"weekdays": 127, "start": "08:00", "end": "12:00"}],
    )  # fmt: skip
    day = TODAY + timedelta(days=1)
    at = datetime(day.year, day.month, day.day, 9, 0, tzinfo=ZoneInfo("Europe/Paris"))
    r = await admin.get(
        f"{p}/api/v1/planning/at",
        params={"target_type": "group", "target_id": w["group"], "at": at.isoformat()},
    )
    assert r.status_code == 200, r.text
    (row,) = r.json()["screens"]
    assert row["screen_name"] == "Hall" and row["presence"]
    seg = row["segment"]
    assert seg["state"] == "play" and seg["source"]["id"] == sid and seg["reason"]
    assert row["next_change_at"] is not None
    assert datetime.fromisoformat(row["next_change_at"]) > at
    off = at - timedelta(hours=6)  # nuit : hors plage
    r = await admin.get(
        f"{p}/api/v1/planning/at",
        params={"target_type": "screen", "target_id": w["scr"], "at": off.isoformat()},
    )
    seg = r.json()["screens"][0]["segment"]
    assert seg["state"] == "off" and seg["reason"] == "no_candidate"
    r = await admin.get(
        f"{p}/api/v1/planning/at",
        params={"target_type": "screen", "target_id": w["scr"], "at": "2026-01-01T10:00:00"},
    )
    assert r.json()["code"] == "planning.at_timezone_required"
    now = await admin.get(
        f"{p}/api/v1/planning/at", params={"target_type": "screen", "target_id": w["scr"]}
    )
    assert now.status_code == 200 and now.json()["screens"][0]["segment"]["state"] in (
        "play",
        "off",
    )


async def test_at_override_wins_then_expires(admin: AsyncClient, p: str) -> None:
    """Forçage actif : état overridden, source override, retour à la programmation à l'échéance."""
    w = await world(admin, p)
    await sched(admin, p, w["pl_site"], "site", w["site"], "Site")
    r = await admin.post(
        f"{p}/api/v1/overrides",
        json={
            "target_type": "screen",
            "target_id": w["scr"],
            "playlist_id": w["pl_scr"],
            "duration_minutes": 60,
        },
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    ov = r.json()
    try:
        q = {"target_type": "screen", "target_id": w["scr"]}
        row = (await admin.get(f"{p}/api/v1/planning/at", params=q)).json()["screens"][0]
        seg = row["segment"]
        assert seg["state"] == "overridden" and seg["source"]["kind"] == "override"
        assert seg["source"]["id"] == ov["id"] and seg["content"]["name"] == "PlEcran"
        assert row["next_change_at"] is not None
        after = datetime.fromisoformat(row["next_change_at"]) + timedelta(minutes=1)
        later = (
            await admin.get(
                f"{p}/api/v1/planning/at", params={**q, "at": after.isoformat()}
            )
        ).json()["screens"][0]["segment"]
        assert later["state"] != "overridden" and later["source"]["kind"] != "override"
    finally:
        await admin.post(f"{p}/api/v1/overrides/{ov['id']}/stop", headers=SAME)


class Counter:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, *_: Any) -> None:
        self.n += 1


@pytest.fixture
def sql_count() -> Iterator[Counter]:
    counter = Counter()
    event.listen(Engine, "before_cursor_execute", counter)
    yield counter
    event.remove(Engine, "before_cursor_execute", counter)


async def test_constant_sql_queries(admin: AsyncClient, p: str, sql_count: Counter) -> None:
    w = await world(admin, p)
    await sched(admin, p, w["pl_site"], "site", w["site"], "Site")
    counts: list[int] = []
    for extra in (1, 8):
        for n in range(extra):
            await screen_of(admin, p, w["site"], f"X{extra}-{n}", w["group"])
        await admin.get(f"{p}/api/v1/planning/timeline", params={})  # chauffe (422)
        for path, q in (("timeline", {"from": FROM, "to": TO}), ("at", {})):
            sql_count.n = 0
            r = await admin.get(
                f"{p}/api/v1/planning/{path}",
                params={"target_type": "site", "target_id": w["site"], **q},
            )
            assert r.status_code == 200
            counts.append(sql_count.n)
    # (timeline, at) pour 2 puis 10 écrans : mêmes nombres
    assert counts[0] == counts[2] and counts[1] == counts[3], counts


async def test_100_screens_42_days_under_one_second(
    admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    w = await world(admin, p)
    await sched(admin, p, w["pl_site"], "site", w["site"], "Site",
                rules=[{"weekdays": 31, "start": "08:00", "end": "18:00"}])  # fmt: skip
    await sched(admin, p, w["pl_scr"], "group", w["group"], "Groupe", priority=10,
                rules=[{"weekdays": 127, "start": "12:00", "end": "14:00"}])  # fmt: skip
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO screen (id, site_id, group_id, name, orientation) "
                "SELECT gen_random_uuid(), :s, :g, 'S' || lpad(g::text, 3, '0'), 'landscape' "
                "FROM generate_series(1, 99) g"
            ),
            {"s": w["site"], "g": w["group"]},
        )
    await engine.dispose()
    end = (TODAY + timedelta(days=42)).isoformat()
    t0 = time.perf_counter()
    r = await tl(admin, p, "site", w["site"], to=end, limit=100)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert len(r.json()["screens"]) == 100 and r.json()["next_cursor"] is None
    assert elapsed < 1.0, elapsed


# --- simulation ----------------------------------------------------------------------------
async def sim(c: AsyncClient, p: str, w: dict[str, str], **body: Any) -> Any:
    payload = {"target_type": "screen", "target_id": w["scr"], "from": FROM, "to": TO, **body}
    return await c.post(f"{p}/api/v1/planning/simulate", json=payload, headers=SAME)


async def counts(migrated_db_url: str) -> tuple[int, int, int]:
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.connect() as c:
        rows = [
            (await c.execute(text("SELECT count(*) FROM schedule"))).scalar_one(),
            (await c.execute(text("SELECT count(*) FROM schedule_rule"))).scalar_one(),
            (await c.execute(text("SELECT count(*) FROM audit_log"))).scalar_one(),
        ]
    await engine.dispose()
    return rows[0], rows[1], rows[2]


async def test_simulate_draft_changes_nothing_written(
    admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    w = await world(admin, p)
    s_site = await sched(admin, p, w["pl_site"], "site", w["site"], "Accueil")
    before = await counts(migrated_db_url)
    draft = {
        "name": "Matin", "playlist_id": w["pl_scr"], "target_type": "screen",
        "target_id": w["scr"], "rules": [{"weekdays": 127, "start": "08:00", "end": "12:00"}],
    }  # fmt: skip
    r = await sim(admin, p, w, draft=draft)
    assert r.status_code == 200, r.text
    out = r.json()
    assert await counts(migrated_db_url) == before
    assert out["screens_touched"] == 1 and out["changes"]
    ch = out["changes"][0]
    assert ch["before"]["source"]["id"] == s_site and ch["before"]["content"]["name"] == "PlSite"
    assert ch["after"]["source"]["kind"] == "draft" and ch["after"]["source"]["id"] is None
    assert ch["after"]["source"]["name"] == "Matin" and ch["after"]["reason"] == "more_specific"
    kinds = {(c["kind"], c["source"]["kind"]) for c in out["new_conflicts"]}
    assert ("content_empty", "draft") in kinds
    assert any(c["kind"] == "partially_masked" for c in out["new_conflicts"])
    assert out["after"]["screens"][0]["segments"]


async def test_simulate_edit_and_remove(admin: AsyncClient, p: str, migrated_db_url: str) -> None:
    w = await world(admin, p)
    s_site = await sched(admin, p, w["pl_site"], "site", w["site"], "Accueil")
    s_scr = await sched(admin, p, w["pl_scr"], "screen", w["scr"], "Ecran")
    before = await counts(migrated_db_url)
    r = await sim(admin, p, w, remove=s_scr)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["screens_touched"] == 1
    assert {c["after"]["content"]["name"] for c in out["changes"]} == {"PlSite"}
    assert any(c["kind"] == "never_plays" for c in out["resolved_conflicts"])
    r = await sim(admin, p, w, edit=[s_scr, {"enabled": False}])
    assert r.status_code == 200 and r.json()["screens_touched"] == 1
    r = await sim(admin, p, w, edit=[s_site, {"playlist_id": w["pl_scr"]}])
    assert r.status_code == 200
    assert r.json()["after"]["screens"][0]["segments"][0]["source"]["id"] == s_scr
    assert await counts(migrated_db_url) == before
    r = await sim(admin, p, w, remove="00000000-0000-4000-8000-000000000001")
    assert r.status_code == 404 and r.json()["code"] == "schedule.not_found"


async def test_simulate_same_validations_as_schedules(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    sid = await sched(admin, p, w["pl_site"], "site", w["site"], "Accueil")
    missing = "00000000-0000-4000-8000-000000000001"
    base_draft = {
        "name": "X", "playlist_id": w["pl_scr"], "target_type": "screen", "target_id": w["scr"]
    }  # fmt: skip
    cases: list[tuple[dict[str, Any], dict[str, Any]]] = [
        ({"draft": {**base_draft, "target_id": missing}}, {}),
        ({"draft": {**base_draft, "playlist_id": missing}}, {}),
        ({"draft": {**base_draft, "starts_on": "2026-02-01", "ends_on": "2026-01-01"}}, {}),
        (
            {"draft": {**base_draft, "rules": [{"weekdays": 1, "start": "10:00", "end": "09:00"}]}},
            {},
        ),
        ({"edit": [sid, {"target_type": "site"}]}, {}),
        ({"edit": [sid, {"target_type": "site", "target_id": missing}]}, {}),
    ]
    for change, _ in cases:
        r = await sim(admin, p, w, **change)
        # la même charge utile envoyée à POST/PATCH /schedules donne la même erreur
        if "draft" in change:
            ref = await admin.post(f"{p}/api/v1/schedules", json=change["draft"], headers=SAME)
        else:
            ref = await admin.patch(
                f"{p}/api/v1/schedules/{change['edit'][0]}", json=change["edit"][1], headers=SAME
            )
        assert r.status_code == ref.status_code == 422, (change, r.text, ref.text)
        if "code" in ref.json():
            assert r.json()["code"] == ref.json()["code"]
        else:
            assert r.json()["detail"][0]["msg"] == ref.json()["detail"][0]["msg"]
    # exactement un changement
    assert (await sim(admin, p, w)).status_code == 422
    assert (await sim(admin, p, w, remove=sid, draft=base_draft)).status_code == 422
    # fenêtre bornée à 14 jours
    r = await sim(admin, p, w, remove=sid, to=(TODAY + timedelta(days=16)).isoformat())
    assert r.status_code == 422 and r.json()["code"] == "planning.window_too_large"


async def test_simulate_origin_check(admin: AsyncClient, p: str) -> None:
    w = await world(admin, p)
    sid = await sched(admin, p, w["pl_site"], "site", w["site"], "Accueil")
    payload = {
        "remove": sid, "target_type": "screen", "target_id": w["scr"], "from": FROM, "to": TO,
    }  # fmt: skip
    r = await admin.post(f"{p}/api/v1/planning/simulate", json=payload)  # ni Origin ni Sec-Fetch
    assert r.status_code == 403 and r.json()["error"]["code"] == "errors.origin_mismatch"
    r = await admin.post(
        f"{p}/api/v1/planning/simulate", json=payload, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert (
        await admin.post(f"{p}/api/v1/planning/simulate", json=payload, headers=SAME)
    ).status_code == 200
