"""`GET agent/plan` (ETag/304, jeton d'agent) et poussée `plan.updated` (aux deux base paths)."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import _queue_clean, auth, setup, sql  # noqa: F401
from test_auth import SAME, admin, anon, app, base, clean, new_client, p  # noqa: F401
from test_power_api import RULE, attach


async def test_agent_plan_route(
    app: FastAPI, admin: AsyncClient, p: str, setup: dict[str, Any]
) -> None:  # noqa: F811
    h = auth(setup["token"])
    async with new_client(app) as agent:
        r = await agent.get(f"{p}/api/v1/agent/plan", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert [s["screen_id"] for s in body["screens"]] == [setup["screen"]]
        etag = r.headers["ETag"]
        assert etag == f'"{body["revision"]}"'
        r = await agent.get(f"{p}/api/v1/agent/plan", headers=h | {"If-None-Match": etag})
        assert r.status_code == 304
        assert (await agent.get(f"{p}/api/v1/agent/plan")).status_code == 401
        bad = auth("vds_agt_inconnu")
        assert (await agent.get(f"{p}/api/v1/agent/plan", headers=bad)).status_code == 401


async def test_plan_updated_pushed(
    app: FastAPI,
    admin: AsyncClient,
    p: str,
    setup: dict[str, Any],
    migrated_db_url: str,
) -> None:
    plan = (
        await admin.post(
            f"{p}/api/v1/power/plans", json={"name": "P", "rules": [RULE]}, headers=SAME
        )
    ).json()["id"]
    await attach(admin, p, "site", setup["site"], "power.plan_id", plan)
    rows = await sql(
        migrated_db_url, "SELECT name, args->>'revision' FROM remote_command ORDER BY seq"
    )
    assert rows and all(r[0] == "plan.updated" for r in rows)
    async with new_client(app) as agent:
        r = await agent.get(f"{p}/api/v1/agent/plan", headers=auth(setup["token"]))
    assert rows[-1][1] == r.json()["revision"]
    # inchangé : pas de nouvelle commande
    await attach(admin, p, "site", setup["site"], "power.plan_id", plan)
    again = await sql(migrated_db_url, "SELECT count(*) FROM remote_command")
    assert again[0][0] == len(rows)
