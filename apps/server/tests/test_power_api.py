"""API planning d'alimentation sous les deux VDS_BASE_PATH ; plan agent."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import (  # noqa: F401
    SAME,
    admin,
    anon,
    app,
    base,
    clean,
    editor,
    new_client,
    p,
)
from test_diffusion import mk_screen
from vds_server.power.plan import PowerPlanBuilder

WEDNESDAY_NOON = "2026-10-21T10:00:00Z"
RULE = {"weekdays": 31, "start": "08:00", "end": "18:00"}


@pytest.fixture(autouse=True)
async def _power_clean(migrated_db_url: str) -> AsyncIterator[None]:
    async def truncate() -> None:
        engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            await c.execute(
                text(
                    "TRUNCATE power_plan, power_exception, power_calendar RESTART IDENTITY CASCADE"
                )
            )
        await engine.dispose()

    await truncate()
    yield
    await truncate()


async def attach(
    admin: AsyncClient, p: str, level: str, target: str, key: str, value: object
) -> None:
    r = await admin.put(
        f"{p}/api/v1/config/{level}/{target}/core/{key}", json={"value": value}, headers=SAME
    )
    assert r.status_code == 204, r.text


async def test_plan_crud_and_access(
    app: FastAPI, admin: AsyncClient, editor: AsyncClient, p: str
) -> None:
    r = await editor.post(
        f"{p}/api/v1/power/plans", json={"name": "Bureau", "rules": [RULE]}, headers=SAME
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["rules"] == [RULE]
    bad = {"name": "X", "rules": [{"weekdays": 0, "start": "08:00", "end": "18:00"}]}
    assert (await editor.post(f"{p}/api/v1/power/plans", json=bad, headers=SAME)).status_code == 422
    r = await editor.patch(
        f"{p}/api/v1/power/plans/{plan['id']}",
        json={"rules": [{**RULE, "end": "24:00"}]},
        headers=SAME,
    )
    assert r.json()["rules"][0]["end"] == "24:00"
    assert len((await editor.get(f"{p}/api/v1/power/plans")).json()) == 1
    r = await editor.get(f"{p}/api/v1/power/plans/{UUID(int=1)}")
    assert r.status_code == 404 and r.json()["code"] == "power.plan_not_found"
    async with new_client(app) as other:
        assert (await other.get(f"{p}/api/v1/power/plans")).status_code == 401
    r = await editor.post(
        f"{p}/api/v1/power/plans", json={"name": "Y"}, headers={"Origin": "https://evil.test"}
    )
    assert r.status_code == 403
    assert (
        await editor.delete(f"{p}/api/v1/power/plans/{plan['id']}", headers=SAME)
    ).status_code == 204


async def test_attachment_inheritance_provenance_and_plan_in_use(
    admin: AsyncClient, p: str
) -> None:
    site, screen = await mk_screen(admin, p)
    plan = (
        await admin.post(
            f"{p}/api/v1/power/plans", json={"name": "Bureau", "rules": [RULE]}, headers=SAME
        )
    ).json()["id"]
    url = f"{p}/api/v1/power/screens/{screen}?at={WEDNESDAY_NOON}"
    none = (await admin.get(url)).json()
    assert none["plan_id"] is None and none["plan_id_origin"]["level"] == "default"
    assert [s["state"] for s in none["slots"]] == ["on"]

    await attach(admin, p, "site", site, "power.plan_id", plan)
    out = (await admin.get(url)).json()
    assert out["plan_id"] == plan
    assert out["plan_id_origin"]["level"] == "site" and out["plan_id_origin"]["id"] == site
    assert out["timezone"] == "Europe/Paris"
    assert [(s["from"][11:16], s["state"]) for s in out["slots"]][:3] == [
        ("00:00", "off"),
        ("08:00", "on"),
        ("18:00", "off"),
    ]
    r = await admin.delete(f"{p}/api/v1/power/plans/{plan}", headers=SAME)
    assert r.status_code == 409 and r.json()["code"] == "power.plan_in_use"

    # exception d'écran prioritaire : journée entière éteinte
    r = await admin.post(
        f"{p}/api/v1/power/exceptions",
        json={
            "scope_level": "screen",
            "scope_id": screen,
            "starts_on": "2026-10-21",
            "ends_on": "2026-10-21",
            "state": "off",
            "label": "Travaux",
        },
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    exc_id = r.json()["id"]
    slots = (await admin.get(url)).json()["slots"]
    assert [(s["from"][:16], s["state"]) for s in slots][:2] == [
        ("2026-10-21T00:00", "off"),
        ("2026-10-22T08:00", "on"),
    ]
    assert len((await admin.get(f"{p}/api/v1/power/exceptions?scope_id={screen}")).json()) == 1
    r = await admin.put(
        f"{p}/api/v1/power/exceptions/{exc_id}",
        json={
            "scope_level": "screen",
            "scope_id": screen,
            "starts_on": "2026-10-21",
            "ends_on": "2026-10-21",
            "start": "09:00",
            "end": "12:00",
            "state": "on",
            "label": "Ouverture",
        },
        headers=SAME,
    )
    assert r.status_code == 200 and r.json()["start"] == "09:00"
    assert (
        await admin.delete(f"{p}/api/v1/power/exceptions/{exc_id}", headers=SAME)
    ).status_code == 204

    # retour à l'héritage au niveau écran, puis plan agent
    await attach(admin, p, "screen", screen, "power.plan_id", None)
    assert (await admin.get(url)).json()["plan_id"] is None


async def test_exception_validation(admin: AsyncClient, p: str) -> None:
    base_body = {
        "scope_level": "site",
        "scope_id": str(UUID(int=7)),
        "starts_on": "2026-10-21",
        "ends_on": "2026-10-21",
        "state": "off",
        "label": "x",
    }
    r = await admin.post(f"{p}/api/v1/power/exceptions", json=base_body, headers=SAME)
    assert r.status_code == 422 and r.json()["code"] == "power.scope_not_found"
    for patch in ({"scope_id": None}, {"start": "09:00"}, {"ends_on": "2026-10-20"}):
        r = await admin.post(
            f"{p}/api/v1/power/exceptions", json={**base_body, **patch}, headers=SAME
        )
        assert r.status_code == 422


async def test_calendar_days_and_effect(editor: AsyncClient, admin: AsyncClient, p: str) -> None:
    site, screen = await mk_screen(admin, p)
    plan = (
        await admin.post(
            f"{p}/api/v1/power/plans", json={"name": "B", "rules": [RULE]}, headers=SAME
        )
    ).json()["id"]
    await attach(admin, p, "site", site, "power.plan_id", plan)
    r = await admin.post(
        f"{p}/api/v1/power/calendars",
        json={"key": "fr.public", "name": {"fr": "Jours fériés", "en": "Public holidays"}},
        headers=SAME,
    )
    assert r.status_code == 201 and r.json()["day_count"] == 0
    dup = await admin.post(
        f"{p}/api/v1/power/calendars", json={"key": "fr.public", "name": {"fr": "x"}}, headers=SAME
    )
    assert dup.status_code == 409
    r = await editor.put(
        f"{p}/api/v1/power/calendars/fr.public/days/2026-10-22",
        json={"label": "Férié"},
        headers=SAME,
    )
    assert r.status_code == 403
    r = await admin.put(
        f"{p}/api/v1/power/calendars/fr.public/days/2026-10-22",
        json={"label": "Férié"},
        headers=SAME,
    )
    assert r.status_code == 200
    await attach(admin, p, "site", site, "power.calendars", ["fr.public"])
    url = f"{p}/api/v1/power/screens/{screen}?at={WEDNESDAY_NOON}"
    out = (await admin.get(url)).json()
    assert out["calendars"] == ["fr.public"] and out["calendars_origin"]["level"] == "site"
    assert [s["state"] for s in out["slots"]] == ["off", "on", "off"]  # jeudi férié : éteint
    assert (await editor.get(f"{p}/api/v1/power/calendars")).json()[0]["day_count"] == 1
    days = (await editor.get(f"{p}/api/v1/power/calendars/fr.public/days")).json()
    assert days == [{"day": "2026-10-22", "label": "Férié"}]
    r = await admin.delete(f"{p}/api/v1/power/calendars/fr.public/days/2026-10-22", headers=SAME)
    assert r.status_code == 204
    r = await admin.delete(f"{p}/api/v1/power/calendars/fr.public/days/2026-10-22", headers=SAME)
    assert r.status_code == 404
    assert (
        await admin.delete(f"{p}/api/v1/power/calendars/fr.public", headers=SAME)
    ).status_code == 204


async def test_agent_plan(app: FastAPI, admin: AsyncClient, p: str) -> None:
    site, screen = await mk_screen(admin, p)
    plan = (
        await admin.post(
            f"{p}/api/v1/power/plans", json={"name": "B", "rules": [RULE]}, headers=SAME
        )
    ).json()["id"]
    await attach(admin, p, "site", site, "power.plan_id", plan)
    from datetime import UTC, datetime

    rt = app.state.runtime
    async with rt.maker() as session:
        body = await PowerPlanBuilder(session, rt.schemas).agent_plan(
            UUID(site), datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
        )
        again = await PowerPlanBuilder(session, rt.schemas).agent_plan(
            UUID(site), datetime(2026, 10, 21, 11, 0, tzinfo=UTC)
        )
    (entry,) = body["screens"]
    assert entry["screen_id"] == screen and entry["timezone"] == "Europe/Paris"
    assert entry["power"][1]["state"] == "on" and entry["cascade"][0] == "power.adb"
    assert body["revision"].startswith("sha256:") and body["revision"] == again["revision"]
