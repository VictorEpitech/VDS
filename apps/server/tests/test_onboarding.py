# ruff: noqa: F811  (fixtures pytest importées de test_auth, comme les autres fichiers)
"""Premiers pas et résumé de parc : étapes calculées, écritures admin, filtre issues."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import DBAPIError
from test_auth import (  # noqa: F401
    SAME,
    admin,
    anon,
    app,
    audit_actions,
    base,
    clean,
    editor,
    new_client,
    p,
)
from test_diffusion import make_media
from test_supervision import sql

STEPS = ["account", "site", "screen", "media", "playlist", "schedule", "power"]
FAR = datetime(2099, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _tables_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    for t in (
        "onboarding_state",
        "schedule",
        "playlist",
        "media",
        "power_exception",
        "power_plan",
        "power_state",
        "incident",
        "site_agent",
        "pairing_request",
    ):
        await sql(migrated_db_url, f"TRUNCATE {t} CASCADE")  # noqa: S608 - littéral de test


def by_step(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["step"]: s for s in body["steps"]}


async def get(c: AsyncClient, p: str) -> dict[str, Any]:
    r = await c.get(f"{p}/api/v1/onboarding")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def put(c: AsyncClient, p: str, path: str, body: Any, **kw: Any) -> Any:
    return await c.put(f"{p}/api/v1/onboarding/{path}", json=body, headers=kw.get("h", SAME))


async def mk_site(c: AsyncClient, p: str, slug: str = "moulins") -> str:
    r = await c.post(f"{p}/api/v1/sites", json={"name": slug, "slug": slug}, headers=SAME)
    return str(r.json()["id"])


async def mk_screen(c: AsyncClient, p: str, site: str, name: str = "Hall") -> str:
    r = await c.post(f"{p}/api/v1/screens", json={"site_id": site, "name": name}, headers=SAME)
    return str(r.json()["id"])


async def pair(url: str, screen: str, seen: datetime | None) -> None:
    await sql(
        url,
        "INSERT INTO screen_device (screen_id, token_hash, platform, paired_at, last_seen_at)"
        " VALUES (:s, :h, 'web', now(), :t)",
        s=UUID(screen),
        h=uuid4().hex,
        t=seen,
    )


async def onboarding_audit(url: str) -> list[str]:
    return [a for a in await audit_actions(url) if a.startswith("onboarding.")]


# --- GET ------------------------------------------------------------------------------
async def test_fresh_defaults_and_get_writes_nothing(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    body = await get(admin, p)
    assert [s["step"] for s in body["steps"]] == STEPS
    st = by_step(body)
    assert st["account"]["status"] == "done" and st["account"]["skippable"] is False
    assert all(st[s]["status"] == "todo" for s in STEPS[1:])
    assert st["power"]["optional"] is True
    assert body["progress"] == {"completed": 1, "total": 6}
    assert body["next_step"] == "site" and body["complete"] is False
    assert body["card"] == {"hidden": False, "hidden_at": None}
    await get(admin, p)
    assert await sql(migrated_db_url, "SELECT 1 FROM onboarding_state") == []
    assert await onboarding_audit(migrated_db_url) == []


async def test_auth_levels(
    admin: AsyncClient,  # noqa: F811
    editor: AsyncClient,  # noqa: F811
    app: Any,  # noqa: F811
    p: str,
) -> None:
    assert (await editor.get(f"{p}/api/v1/onboarding")).status_code == 200
    assert (await editor.get(f"{p}/api/v1/fleet/summary")).status_code == 200
    for path, body in (("steps/site", {"skipped": True}), ("card", {"hidden": True})):
        assert (await put(editor, p, path, body)).status_code == 403, path
    async with new_client(app) as other:
        assert (await other.get(f"{p}/api/v1/onboarding")).status_code == 401
        assert (await other.get(f"{p}/api/v1/fleet/summary")).status_code == 401


async def test_put_needs_same_origin(admin: AsyncClient, p: str) -> None:  # noqa: F811
    evil = {"Origin": "https://evil.test"}
    for path, body in (("steps/site", {"skipped": True}), ("card", {"hidden": True})):
        assert (await put(admin, p, path, body, h=evil)).status_code == 403
    assert (await get(admin, p))["card"]["hidden"] is False


async def test_steps_progress_on_real_data(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    url = migrated_db_url
    site = await mk_site(admin, p)
    st = by_step(await get(admin, p))
    assert st["site"]["status"] == "done" and st["site"]["count"] == 1
    assert st["site"]["details"] == {"agent_connected": False}

    screen = await mk_screen(admin, p, site)
    assert by_step(await get(admin, p))["screen"]["status"] == "in_progress"
    await pair(url, screen, datetime.now(UTC))
    st = by_step(await get(admin, p))
    assert st["screen"]["status"] == "done" and st["screen"]["count"] == 1

    await sql(
        url,
        "INSERT INTO pairing_request (code, poll_secret_hash, status, expires_at)"
        " VALUES ('ABCD-1234', 'x', 'pending', :t)",
        t=FAR,
    )
    assert by_step(await get(admin, p))["screen"]["details"] == {"pending_pairing": 1}

    await make_media(url)
    assert by_step(await get(admin, p))["media"]["status"] == "done"

    pl = (await sql(url, "INSERT INTO playlist (name) VALUES ('P') RETURNING id"))[0][0]
    assert by_step(await get(admin, p))["playlist"]["status"] == "in_progress"
    await sql(
        url,
        "INSERT INTO playlist_item (playlist_id, position, content_type) VALUES (:p, 0, 'x')",
        p=pl,
    )
    assert by_step(await get(admin, p))["playlist"]["status"] == "done"

    # programmation qui ne couvre aucun écran (site sans écran), puis couvrante
    other = await mk_site(admin, p, "autre")
    await sql(
        url,
        "INSERT INTO schedule (name, playlist_id, target_type, target_id)"
        " VALUES ('S', :p, 'site', :t)",
        p=pl,
        t=UUID(other),
    )
    assert by_step(await get(admin, p))["schedule"]["status"] == "in_progress"
    await sql(
        url,
        "INSERT INTO schedule (name, playlist_id, target_type, target_id)"
        " VALUES ('S2', :p, 'screen', :t)",
        p=pl,
        t=UUID(screen),
    )
    st = by_step(await get(admin, p))
    assert st["schedule"]["status"] == "done" and st["schedule"]["count"] == 1

    body = await get(admin, p)
    assert body["complete"] is True and body["next_step"] == "power"
    assert body["progress"] == {"completed": 6, "total": 6}
    await sql(
        url,
        "INSERT INTO power_exception (scope_level, starts_on, ends_on, state, label)"
        " VALUES ('organization', '2026-12-25', '2026-12-25', 'off', 'Noël')",
    )
    body = await get(admin, p)
    assert by_step(body)["power"]["status"] == "done" and body["next_step"] is None


async def test_schedule_cover_org_and_archived(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    url = migrated_db_url
    site = await mk_site(admin, p)
    screen = await mk_screen(admin, p, site)
    pl = (await sql(url, "INSERT INTO playlist (name) VALUES ('P') RETURNING id"))[0][0]
    org = (await sql(url, "SELECT id FROM organization"))[0][0]
    await sql(
        url,
        "INSERT INTO schedule (name, playlist_id, target_type, target_id)"
        " VALUES ('O', :p, 'organization', :t)",
        p=pl,
        t=org,
    )
    assert by_step(await get(admin, p))["schedule"]["status"] == "done"
    await sql(url, "UPDATE screen SET archived_at = now() WHERE id = :s", s=UUID(screen))
    assert by_step(await get(admin, p))["schedule"]["status"] == "in_progress"


async def test_power_rule_counts_and_agent_connected(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    url = migrated_db_url
    site = await mk_site(admin, p)
    plan = (await sql(url, "INSERT INTO power_plan (name) VALUES ('B') RETURNING id"))[0][0]
    assert by_step(await get(admin, p))["power"]["status"] == "todo"
    await sql(
        url,
        "INSERT INTO power_plan_rule (plan_id, weekdays, start_minute, end_minute)"
        " VALUES (:p, 31, 480, 1080)",
        p=plan,
    )
    assert by_step(await get(admin, p))["power"]["status"] == "done"
    await sql(
        url,
        "INSERT INTO site_agent (site_id, name, token_hash, token_prefix, last_seen_at)"
        " VALUES (:s, 'a', 'h', 'p', now())",
        s=UUID(site),
    )
    assert by_step(await get(admin, p))["site"]["details"] == {"agent_connected": True}


# --- PUT ------------------------------------------------------------------------------
async def test_skip_reopen_audit_and_priority(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    r = await put(admin, p, "steps/playlist", {"skipped": True})
    assert r.status_code == 200
    body = r.json()
    assert by_step(body)["playlist"]["status"] == "skipped"
    assert body["progress"] == {"completed": 2, "total": 6}
    assert body["next_step"] == "site"
    await put(admin, p, "steps/playlist", {"skipped": True})  # idempotent : un seul audit
    # ignorée puis réalisée = faite
    await sql(migrated_db_url, "INSERT INTO playlist (name) VALUES ('P')")
    await sql(
        migrated_db_url,
        "INSERT INTO playlist_item (playlist_id, position, content_type)"
        " SELECT id, 0, 'x' FROM playlist",
    )
    assert by_step(await get(admin, p))["playlist"]["status"] == "done"
    assert (await put(admin, p, "steps/playlist", {"skipped": False})).status_code == 200
    rows = await sql(migrated_db_url, "SELECT skipped_steps, updated_by FROM onboarding_state")
    assert rows[0][0] == [] and rows[0][1] is not None
    assert await onboarding_audit(migrated_db_url) == [
        "onboarding.step_skipped",
        "onboarding.step_reopened",
    ]


async def test_skip_errors(admin: AsyncClient, p: str) -> None:  # noqa: F811
    r = await put(admin, p, "steps/account", {"skipped": True})
    assert r.status_code == 409 and r.json()["code"] == "onboarding.step_not_skippable"
    assert (await put(admin, p, "steps/account", {"skipped": False})).status_code == 200
    r = await put(admin, p, "steps/nope", {"skipped": True})
    assert r.status_code == 404 and r.json()["code"] == "onboarding.step_unknown"
    assert (await put(admin, p, "steps/site", {"skipped": "x"})).status_code == 422
    assert (await put(admin, p, "card", {})).status_code == 422


async def test_complete_by_skipping_and_card(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    for s in STEPS[1:6]:
        r = await put(admin, p, f"steps/{s}", {"skipped": True})
    assert r.json()["complete"] is True and r.json()["next_step"] == "power"
    r = await put(admin, p, "card", {"hidden": True})
    assert r.json()["card"]["hidden"] is True and r.json()["card"]["hidden_at"]
    first = r.json()["card"]["hidden_at"]
    r = await put(admin, p, "card", {"hidden": True})
    assert r.json()["card"]["hidden_at"] == first  # idempotent
    r = await put(admin, p, "card", {"hidden": False})
    assert r.json()["card"] == {"hidden": False, "hidden_at": None}
    actions = await onboarding_audit(migrated_db_url)
    assert actions[-2:] == ["onboarding.card_hidden", "onboarding.card_shown"]


async def test_check_rejects_unknown_or_account_step(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    org = (await sql(migrated_db_url, "SELECT id FROM organization"))[0][0]
    for bad in ("{legacy,media}", "{account}"):
        with pytest.raises(DBAPIError):
            await sql(
                migrated_db_url,
                "INSERT INTO onboarding_state (organization_id, skipped_steps) VALUES (:o, :s)",
                o=org,
                s=bad,
            )
    await sql(
        migrated_db_url,
        "INSERT INTO onboarding_state (organization_id, skipped_steps) VALUES (:o, '{media}')",
        o=org,
    )
    assert by_step(await get(admin, p))["media"]["status"] == "skipped"


async def test_concurrent_skips_do_not_lose_updates(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    steps = ["site", "screen", "media", "playlist", "schedule", "power"]
    rs = await asyncio.gather(*(put(admin, p, f"steps/{s}", {"skipped": True}) for s in steps))
    assert all(r.status_code == 200 for r in rs)
    rows = await sql(migrated_db_url, "SELECT skipped_steps FROM onboarding_state")
    assert rows[0][0] == steps


# --- Fleet ----------------------------------------------------------------------------
async def fleet(c: AsyncClient, p: str, site: str | None = None) -> dict[str, Any]:
    q = f"?site_id={site}" if site else ""
    r = await c.get(f"{p}/api/v1/fleet/summary{q}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def issues(c: AsyncClient, p: str, q: str = "issues=true") -> list[str]:
    r = await c.get(f"{p}/api/v1/screens?{q}")
    assert r.status_code == 200, r.text
    return [s["name"] for s in r.json()]


async def test_fleet_summary_and_issues_filter(
    admin: AsyncClient,  # noqa: F811
    p: str,
    migrated_db_url: str,
) -> None:
    url = migrated_db_url
    now = datetime.now(UTC)
    s1, s2 = await mk_site(admin, p, "s1"), await mk_site(admin, p, "s2")
    ok = await mk_screen(admin, p, s1, "ok")
    off = await mk_screen(admin, p, s1, "off")
    await mk_screen(admin, p, s2, "unseen")
    pf = await mk_screen(admin, p, s2, "pf")
    rev = await mk_screen(admin, p, s2, "rev")
    sched = await mk_screen(admin, p, s2, "sched")
    await pair(url, ok, now)
    await pair(url, off, now - timedelta(hours=2))
    await pair(url, pf, now)
    await pair(url, sched, now - timedelta(hours=2))
    await sql(
        url,
        "INSERT INTO screen_device (screen_id, token_hash, platform, paired_at, revoked_at)"
        " VALUES (:s, 'r', 'web', now(), now())",
        s=UUID(rev),
    )
    await sql(
        url,
        "INSERT INTO power_state (screen_id, desired, desired_reason, desired_since)"
        " VALUES (:s, 'off', 'plan', now())"
        " ON CONFLICT (screen_id) DO UPDATE SET desired = 'off', desired_since = now()",
        s=UUID(sched),
    )
    await sql(
        url,
        "INSERT INTO power_state (screen_id, desired, desired_reason, desired_since)"
        " VALUES (:s, 'on', 'plan', :t)"
        " ON CONFLICT (screen_id) DO UPDATE SET desired = 'on', desired_since = :t",
        s=UUID(off),
        t=now - timedelta(hours=3),
    )
    site2 = UUID(s2)
    await sql(
        url,
        "INSERT INTO incident (kind, severity, subject_type, subject_id, site_id, opened_at)"
        " VALUES ('power.failed', 'warning', 'screen', :pf, :s, now()),"
        " ('agent.offline', 'critical', 'agent', :a, :s, now()),"
        " ('module.quarantined', 'critical', 'module', 'm', NULL, now())",
        pf=pf,
        a=str(uuid4()),
        s=site2,
    )
    await sql(url, "UPDATE incident SET acknowledged_at = now() WHERE kind = 'agent.offline'")
    await sql(
        url,
        "INSERT INTO site_agent (site_id, name, token_hash, token_prefix, last_seen_at)"
        " VALUES (:s, 'a', 'h', 'p', :t)",
        s=site2,
        t=now - timedelta(hours=1),
    )

    full = await fleet(admin, p)
    assert full["site_id"] is None
    assert full["screens"] == {
        "total": 6,
        "by_status": {"online": 2, "offline": 1, "scheduled_off": 1, "unseen": 1, "revoked": 1},
    }
    assert full["to_handle"] == {
        "screens": 3,
        "by_reason": {"offline": 1, "revoked": 1, "power_failed": 1},
    }
    assert full["incidents"]["open"] == 3 and full["incidents"]["unacknowledged"] == 2
    assert full["incidents"]["by_severity"] == {"warning": 1, "critical": 2}
    assert full["agents"] == {"total": 1, "online": 0, "offline": 1}

    listed = await issues(admin, p)
    assert sorted(listed) == ["off", "pf", "rev"]
    assert len(listed) == full["to_handle"]["screens"]  # cohérence stricte

    part = await fleet(admin, p, s2)
    assert part["site_id"] == s2 and part["screens"]["total"] == 4
    assert part["incidents"]["open"] == 2  # module.quarantined (sans site) exclu
    assert "module.quarantined" not in part["incidents"]["by_kind"]
    assert part["to_handle"]["screens"] == 2
    assert sorted(await issues(admin, p, f"issues=true&site_id={s2}")) == ["pf", "rev"]
    p1 = await fleet(admin, p, s1)
    assert p1["to_handle"]["screens"] == 1 and p1["agents"]["total"] == 0
    assert await issues(admin, p, f"issues=true&site_id={s1}") == ["off"]

    assert await issues(admin, p, "status=offline&status=revoked") == ["off", "rev"]
    assert await issues(admin, p, "status=scheduled_off") == ["sched"]
    assert len(await issues(admin, p, "include_archived=false")) == 6
    assert (await admin.get(f"{p}/api/v1/screens?status=bogus")).status_code == 422
    # un écran archivé sort du résumé et du filtre
    await sql(url, "UPDATE screen SET archived_at = now() WHERE id = :s", s=UUID(off))
    assert "off" not in await issues(admin, p)
    assert (await fleet(admin, p))["to_handle"]["screens"] == 2


async def test_fleet_empty_and_unseen_not_to_handle(
    admin: AsyncClient,  # noqa: F811
    p: str,
) -> None:
    empty = await fleet(admin, p)
    assert empty["screens"]["total"] == 0 and empty["to_handle"]["screens"] == 0
    assert empty["incidents"] == {"open": 0, "unacknowledged": 0, "by_severity": {}, "by_kind": {}}
    site = await mk_site(admin, p)
    await mk_screen(admin, p, site)
    assert (await fleet(admin, p))["screens"]["by_status"]["unseen"] == 1
    assert await issues(admin, p) == []


async def test_fleet_site_errors(admin: AsyncClient, p: str) -> None:  # noqa: F811
    r = await admin.get(f"{p}/api/v1/fleet/summary?site_id={uuid4()}")
    assert r.status_code == 404 and r.json()["code"] == "site.not_found"
    site = await mk_site(admin, p)
    await admin.patch(f"{p}/api/v1/sites/{site}", json={"archived": True}, headers=SAME)
    r = await admin.get(f"{p}/api/v1/fleet/summary?site_id={site}")
    assert r.status_code == 404 and r.json()["code"] == "site.not_found"
    assert (await admin.get(f"{p}/api/v1/fleet/summary?site_id=nope")).status_code == 422
