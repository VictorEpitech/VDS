"""power_state / power_action alimentés par le plan résolu et les événements ; état de module."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import mk_screen
from vds_sdk import Event
from vds_server.core.runtime import Runtime
from vds_server.db.models import AuditLog
from vds_server.db.models_power import PowerAction, PowerState

OFF_AT = datetime(2026, 3, 2, 3, 0, tzinfo=UTC)  # 04:00 à Paris
ON_AT = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)  # 11:00 à Paris


def rt_of(app: FastAPI) -> Runtime:
    rt: Runtime = app.state.runtime
    return rt


async def state_of(rt: Runtime, screen: str) -> PowerState | None:
    async with rt.maker() as s:
        return await s.get(PowerState, UUID(screen))


async def test_desired_follows_resolved_plan(admin: AsyncClient, app: FastAPI, p: str) -> None:  # noqa: F811
    rt = rt_of(app)
    site, screen = await mk_screen(admin, p)
    await rt.power_state.refresh(ON_AT)
    row = await state_of(rt, screen)
    assert row and row.desired == "on" and row.observed == "unknown"  # sans plan : allumé

    r = await admin.post(
        f"{p}/api/v1/power/plans",
        json={"name": "Bureau", "rules": [{"weekdays": 127, "start": "08:00", "end": "18:00"}]},
        headers=SAME,
    )
    plan = r.json()["id"]
    r = await admin.put(
        f"{p}/api/v1/config/site/{site}/core/power.plan_id",
        json={"value": plan},
        headers=SAME,
    )
    assert r.status_code == 204, r.text
    await rt.power_state.refresh(ON_AT)
    row = await state_of(rt, screen)
    assert row and row.desired == "on"
    since = row.desired_since
    await rt.power_state.refresh(OFF_AT)
    row = await state_of(rt, screen)
    assert (
        row
        and row.desired == "off"
        and row.desired_since == datetime(2026, 3, 1, 23, 0, tzinfo=UTC) != since
    )
    await rt.power_state.refresh(OFF_AT)  # inchangé : desired_since conservé
    row = await state_of(rt, screen)
    assert row and row.desired_since == datetime(2026, 3, 1, 23, 0, tzinfo=UTC)


async def test_events_write_observed_and_actions(admin: AsyncClient, app: FastAPI, p: str) -> None:  # noqa: F811
    rt = rt_of(app)
    site, screen = await mk_screen(admin, p)
    rid = uuid4()
    origin = f"agent:{site}"
    changed = Event(
        name="power.changed",
        payload={
            "screen_id": screen,
            "state": "off",
            "driver": "power.adb",
            "verified": True,
            "request_id": str(rid),
        },
        source="agent",
        origin=origin,
    )
    await rt.bus.publish(changed)
    row = await state_of(rt, screen)
    assert row and row.observed == "off" and row.driver == "power.adb" and row.verified
    assert row.observed_at is not None and row.last_code is None
    failed = Event(
        name="power.failed",
        payload={"screen_id": screen, "state": "on", "attempted": ["power.adb"], "code": "x.y"},
        source="agent",
        origin=origin,
    )
    await rt.bus.publish(failed)
    row = await state_of(rt, screen)
    assert row and row.observed == "off" and row.last_code == "x.y"
    async with rt.maker() as s:
        acts = list(
            (
                await s.execute(
                    select(PowerAction)
                    .where(PowerAction.screen_id == UUID(screen))
                    .order_by(PowerAction.at)
                )
            ).scalars()
        )
    assert [(a.state, a.ok, a.verified, a.code, a.origin) for a in acts] == [
        ("off", True, True, None, origin),
        ("on", False, False, "x.y", origin),
    ]
    assert acts[0].request_id == rid and acts[0].reason == "schedule"
    # événement rejoué (outbox de l'agent) : une seule ligne de journal
    await rt.bus.publish(changed.model_copy(update={"id": None}))
    async with rt.maker() as s:
        n = len(list((await s.execute(select(PowerAction.id))).scalars()))
    assert n == 2


async def test_module_state_route(
    admin: AsyncClient, editor: AsyncClient, app: FastAPI, p: str
) -> None:  # noqa: F811
    rt = rt_of(app)
    url = f"{p}/api/v1/registry/modules/notify.mail/state"
    assert (await editor.put(url, json={"state": "enabled"}, headers=SAME)).status_code == 403
    assert (await admin.put(url, json={"state": "bogus"}, headers=SAME)).status_code == 422
    try:
        r = await admin.put(url, json={"state": "enabled"}, headers=SAME)
        assert r.status_code == 200, r.text
        assert r.json() == {"id": "notify.mail", "state": "enabled", "last_error": None}
        assert rt.registry.is_running("notify.mail")
        r = await admin.put(url, json={"state": "enabled"}, headers=SAME)  # idempotent
        assert r.status_code == 200
    finally:
        r = await admin.put(url, json={"state": "disabled"}, headers=SAME)
    assert r.status_code == 200 and r.json()["state"] == "disabled"
    assert not rt.registry.is_running("notify.mail")
    r = await admin.put(
        f"{p}/api/v1/registry/modules/inconnu/state", json={"state": "enabled"}, headers=SAME
    )
    assert (r.status_code, r.json()["code"]) == (404, "module.not_found")
    r = await admin.put(
        f"{p}/api/v1/registry/modules/auth.local/state", json={"state": "disabled"}, headers=SAME
    )
    assert (r.status_code, r.json()["code"]) == (409, "module.required_by_settings")
    async with rt.maker() as s:
        actions = set((await s.execute(select(AuditLog.action))).scalars())
    assert {"module.enabled", "module.disabled"} <= actions
