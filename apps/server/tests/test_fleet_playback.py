"""GET /api/v1/fleet/playback : programmation active par écran, aux deux VDS_BASE_PATH."""
# ruff: noqa: F811

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, _diffusion_clean, mk_playlist  # noqa: F401
from vds_server.fleet.playback import ScreenPlaybackOut, fleet_playback


async def site_of(c: AsyncClient, p: str, slug: str) -> str:
    r = await c.post(f"{p}/api/v1/sites", json={"name": slug, "slug": slug}, headers=SAME)
    return str(r.json()["id"])


async def screen_of(c: AsyncClient, p: str, site: str, name: str, group: str | None = None) -> str:
    body = {"site_id": site, "name": name, **({"group_id": group} if group else {})}
    r = await c.post(f"{p}/api/v1/screens", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def schedule(
    c: AsyncClient, p: str, pl: str, tt: str, tid: str, name: str, priority: int = 0
) -> str:
    body = {
        "name": name,
        "playlist_id": pl,
        "target_type": tt,
        "target_id": tid,
        "priority": priority,
    }
    r = await c.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def playback(c: AsyncClient, p: str, query: str = "") -> dict[str, dict[str, object]]:
    r = await c.get(f"{p}/api/v1/fleet/playback{query}")
    assert r.status_code == 200, r.text
    return {row["screen_id"]: row for row in r.json()}


async def test_no_schedule(admin: AsyncClient, p: str) -> None:
    s = await screen_of(admin, p, await site_of(admin, p, "a"), "Hall")
    row = (await playback(admin, p))[s]
    assert row["source"] == "none" and row["schedule_id"] is None
    assert row["playlist_name"] is None and row["next_change_at"] is None


async def test_priority_and_inheritance(admin: AsyncClient, p: str) -> None:
    site = await site_of(admin, p, "a")
    grp = (
        await admin.post(f"{p}/api/v1/sites/{site}/groups", json={"name": "G"}, headers=SAME)
    ).json()["id"]
    in_group = await screen_of(admin, p, site, "Groupe", grp)
    alone = await screen_of(admin, p, site, "Seul")
    pl_site = await mk_playlist(admin, p, "PlSite")
    pl_grp = await mk_playlist(admin, p, "PlGroupe")
    pl_scr = await mk_playlist(admin, p, "PlEcran")
    s_site = await schedule(admin, p, pl_site, "site", site, "Site", priority=99)
    s_grp = await schedule(admin, p, pl_grp, "group", grp, "Groupe")

    rows = await playback(admin, p)
    assert rows[alone]["schedule_id"] == s_site and rows[alone]["playlist_name"] == "PlSite"
    # le groupe est plus spécifique que le site, malgré la priorité numérique du site
    assert rows[in_group]["schedule_id"] == s_grp and rows[in_group]["source"] == "schedule"
    assert rows[in_group]["schedule_name"] == "Groupe" and rows[in_group]["until"] is not None

    s_scr = await schedule(admin, p, pl_scr, "screen", in_group, "Ecran")
    rows = await playback(admin, p)
    assert rows[in_group]["schedule_id"] == s_scr and rows[in_group]["playlist_name"] == "PlEcran"
    assert rows[alone]["schedule_id"] == s_site


async def test_site_filter(admin: AsyncClient, p: str) -> None:
    a, b = await site_of(admin, p, "a"), await site_of(admin, p, "b")
    sa = await screen_of(admin, p, a, "A1")
    sb = await screen_of(admin, p, b, "B1")
    assert set(await playback(admin, p)) == {sa, sb}
    assert set(await playback(admin, p, f"?site_id={a}")) == {sa}
    r = await admin.get(f"{p}/api/v1/fleet/playback?site_id=00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["code"] == "site.not_found"


async def test_archived_site_excluded(admin: AsyncClient, p: str) -> None:
    a, b = await site_of(admin, p, "a"), await site_of(admin, p, "b")
    sa = await screen_of(admin, p, a, "A1")
    sb = await screen_of(admin, p, b, "B1")
    r = await admin.patch(f"{p}/api/v1/sites/{b}", json={"archived": True}, headers=SAME)
    assert r.status_code == 200, r.text
    assert set(await playback(admin, p)) == {sa}
    r = await admin.get(f"{p}/api/v1/fleet/playback?site_id={b}")
    assert r.status_code == 404 and r.json()["code"] == "site.not_found"
    assert sb not in await playback(admin, p)


async def test_rights(admin: AsyncClient, editor: AsyncClient, p: str) -> None:
    s = await screen_of(admin, p, await site_of(admin, p, "a"), "Hall")
    assert s in await playback(editor, p)
    async with new_client(admin._transport.app) as other:  # type: ignore[attr-defined]
        assert (await other.get(f"{p}/api/v1/fleet/playback")).status_code == 401


# --- règles horaires : next_change_at, hors plage, fuseau Europe/Paris ------------------------
async def _tz_paris(_: UUID) -> object:
    return "Europe/Paris"


async def _at(app: FastAPI, screen: str, when: str) -> ScreenPlaybackOut:
    """Programmation active d'un écran à l'instant UTC `when` (horloge injectée)."""
    async with app.state.runtime.maker() as session:
        rows = await fleet_playback(session, None, datetime.fromisoformat(when), _tz_paris)
    return next(r for r in rows if str(r.screen_id) == screen)


def _utc(iso: str | None) -> datetime | None:
    return None if iso is None else datetime.fromisoformat(iso).astimezone(UTC)


async def _sites(admin: AsyncClient, p: str) -> list[object]:
    return list((await admin.get(f"{p}/api/v1/sites")).json())


async def _ruled(
    admin: AsyncClient, p: str, rules: list[dict[str, object]], **extra: object
) -> str:
    slug = f"r{len(await _sites(admin, p))}"
    site = await site_of(admin, p, slug)
    screen = await screen_of(admin, p, site, "Hall")
    pl = await mk_playlist(admin, p, "Pl")
    body = {
        "name": "Horaire",
        "playlist_id": pl,
        "target_type": "screen",
        "target_id": screen,
        "rules": rules,
        **extra,
    }
    r = await admin.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    return screen


WEEK = [{"weekdays": 31, "start": "08:00", "end": "19:00"}]  # lundi a vendredi


async def test_in_range_gives_end_and_next_change(admin: AsyncClient, app: FastAPI, p: str) -> None:
    screen = await _ruled(admin, p, WEEK)
    # mercredi 2026-11-04 10:00 a Paris (UTC+1) = 09:00 UTC
    row = await _at(app, screen, "2026-11-04T09:00:00+00:00")
    assert row.source == "schedule" and row.schedule_name == "Horaire"
    assert row.until == datetime(2026, 11, 4, 18, 0, tzinfo=UTC)  # 19:00 Paris
    assert row.next_change_at == datetime(2026, 11, 4, 18, 0, tzinfo=UTC)


async def test_out_of_range_is_none_with_next_start(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    screen = await _ruled(admin, p, WEEK)
    # mercredi 20:00 Paris : hors plage, reprise jeudi 08:00 Paris = 07:00 UTC
    row = await _at(app, screen, "2026-11-04T19:00:00+00:00")
    assert row.source == "none" and row.schedule_id is None and row.playlist_name is None
    assert row.until == datetime(2026, 11, 5, 7, 0, tzinfo=UTC)
    assert row.next_change_at == datetime(2026, 11, 5, 7, 0, tzinfo=UTC)


async def test_boundaries_are_start_inclusive_end_exclusive(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    screen = await _ruled(admin, p, WEEK)
    assert (await _at(app, screen, "2026-11-04T07:00:00+00:00")).source == "schedule"  # 08:00 pile
    assert (await _at(app, screen, "2026-11-04T06:59:59+00:00")).source == "none"
    assert (await _at(app, screen, "2026-11-04T18:00:00+00:00")).source == "none"  # 19:00 pile
    assert (await _at(app, screen, "2026-11-04T17:59:59+00:00")).source == "schedule"


async def test_weekend_has_no_next_change_in_window(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    screen = await _ruled(admin, p, WEEK)
    # samedi 12:00 Paris : la fenetre (samedi + dimanche) ne contient aucun creneau actif
    row = await _at(app, screen, "2026-11-07T11:00:00+00:00")
    assert row.source == "none" and row.next_change_at is None
    assert row.until == datetime(2026, 11, 8, 23, 0, tzinfo=UTC)  # lundi 00:00 Paris


async def test_day_change_at_paris_midnight(admin: AsyncClient, app: FastAPI, p: str) -> None:
    screen = await _ruled(admin, p, [{"weekdays": 127, "start": "00:00", "end": "24:00"}])
    # 23:30 Paris (22:30 UTC) : actif, la bascule de jour ne coupe pas le creneau
    row = await _at(app, screen, "2026-11-04T22:30:00+00:00")
    assert row.source == "schedule"


async def test_dst_spring_forward_uses_paris_wall_clock(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    screen = await _ruled(admin, p, [{"weekdays": 127, "start": "08:00", "end": "19:00"}])
    # samedi 2026-03-28 20:00 Paris (UTC+1) ; le dimanche 29 passe a UTC+2
    row = await _at(app, screen, "2026-03-28T19:00:00+00:00")
    assert row.source == "none"
    assert row.next_change_at == datetime(2026, 3, 29, 6, 0, tzinfo=UTC)  # 08:00 Paris = 06:00 UTC


async def test_dst_fall_back_uses_paris_wall_clock(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    screen = await _ruled(admin, p, [{"weekdays": 127, "start": "08:00", "end": "19:00"}])
    # samedi 2026-10-24 20:00 Paris (UTC+2) ; le dimanche 25 repasse a UTC+1
    row = await _at(app, screen, "2026-10-24T18:00:00+00:00")
    assert row.source == "none"
    assert row.next_change_at == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)  # 08:00 Paris = 07:00 UTC
    # le jour du changement, le creneau 08:00-19:00 dure bien 11 h murales (12 h UTC)
    inside = await _at(app, screen, "2026-10-25T08:00:00+00:00")
    assert inside.source == "schedule" and inside.until == datetime(2026, 10, 25, 18, 0, tzinfo=UTC)


async def test_outside_validity_dates_and_disabled(
    admin: AsyncClient, app: FastAPI, p: str
) -> None:
    ended = await _ruled(admin, p, [], ends_on="2026-11-01")
    row = await _at(app, ended, "2026-11-04T09:00:00+00:00")
    assert row.source == "none" and row.next_change_at is None
    # un schedule sans regle est actif toute la journee tant qu'il est valide
    ok = await _ruled(admin, p, [], starts_on="2026-11-01")
    assert (await _at(app, ok, "2026-11-04T09:00:00+00:00")).source == "schedule"
