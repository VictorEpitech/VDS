"""Supervision : statuts, incidents (un ouvert par clé), API, notify.mail de bout en bout."""

import importlib.util
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import mk_screen
from vds_sdk import Event
from vds_server.core.runtime import Runtime
from vds_server.supervision import Thresholds, screen_status

NOW = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
TH = Thresholds()
FAKES = Path(__file__).resolve().parents[3] / "modules" / "notify.mail" / "tests" / "fakes.py"


def _fakes() -> Any:
    spec = importlib.util.spec_from_file_location("notify_mail_fakes_srv", FAKES)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["notify_mail_fakes_srv"] = mod
    spec.loader.exec_module(mod)
    return mod


# --- fonction pure -------------------------------------------------------------------
def status(**over: Any) -> str:
    args: dict[str, Any] = {
        "has_active_device": True,
        "has_revoked_device": False,
        "last_seen_at": NOW - timedelta(minutes=30),
        "desired": "on",
        "desired_since": NOW - timedelta(hours=2),
        "now": NOW,
        "thresholds": TH,
    }
    return str(screen_status(**(args | over)))


def test_screen_status_matrix() -> None:
    assert status() == "offline"
    assert status(last_seen_at=NOW - timedelta(seconds=60)) == "online"
    assert status(last_seen_at=NOW - timedelta(seconds=179)) == "online"
    assert status(last_seen_at=NOW - timedelta(seconds=180)) == "offline"
    assert status(desired="off") == "scheduled_off"  # aucune alerte, même sans heartbeat
    assert status(desired_since=NOW - timedelta(seconds=599)) == "online"  # grâce de démarrage
    assert status(desired_since=NOW - timedelta(seconds=600)) == "offline"
    assert status(desired=None, desired_since=None) == "offline"  # pas de plan : toujours allumé
    assert status(last_seen_at=None) == "unseen"
    assert status(has_active_device=False) == "unseen"
    assert status(has_active_device=False, has_revoked_device=True) == "revoked"


def test_thresholds_from_config() -> None:
    assert Thresholds.from_config({}) == Thresholds(600, 180, 300)
    cfg = {"supervision.grace_s": 60, "supervision.agent_offline_after_s": True}
    assert Thresholds.from_config(cfg) == Thresholds(60, 180, 300)  # bool refusé


# --- base ----------------------------------------------------------------------------
async def sql(url: str, query: str, **params: Any) -> list[Any]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        result = await c.execute(text(query), params)
        rows = list(result.all()) if result.returns_rows else []
    await engine.dispose()
    return rows


@pytest.fixture(autouse=True)
async def _incident_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    await sql(migrated_db_url, "TRUNCATE incident CASCADE")
    await sql(migrated_db_url, "TRUNCATE site_agent CASCADE")


@pytest.fixture
async def world(admin: AsyncClient, p: str, migrated_db_url: str) -> dict[str, Any]:  # noqa: F811
    site, screen = await mk_screen(admin, p)
    await sql(
        migrated_db_url,
        "INSERT INTO screen_device (screen_id, token_hash, platform, paired_at, last_seen_at)"
        " VALUES (:s, :h, 'web', :t, :t)",
        s=UUID(screen),
        h=uuid4().hex,
        t=NOW - timedelta(hours=1),
    )
    # le suivi d'alimentation du serveur a déjà posé sa ligne (horloge réelle) : on repart d'un
    # état « non calculé » pour que ces tests pilotent l'horloge et power_state eux-mêmes
    await sql(migrated_db_url, "DELETE FROM power_state")
    return {"site": site, "screen": screen, "url": migrated_db_url}


class Spy:
    def __init__(self, rt: Runtime) -> None:
        self.events: list[Event] = []
        for name in ("incident.opened", "incident.resolved", "screen.status_changed"):
            rt.bus.subscribe(name, self._on, subscriber="spy")

    async def _on(self, event: Event) -> None:
        self.events.append(event)

    def names(self) -> list[str]:
        return [e.name for e in self.events]


async def incidents(url: str, state: str = "") -> list[Any]:
    return await sql(
        url,
        "SELECT kind, severity, subject_type, resolved_at IS NOT NULL FROM incident " + state,  # noqa: S608 - littéral de test
    )


async def test_offline_opens_one_incident_then_resolves(
    app: FastAPI,
    world: dict[str, Any],  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    spy = Spy(rt)
    url = world["url"]
    await rt.supervisor.tick(NOW - timedelta(minutes=59))  # en ligne : état initial mémorisé
    await rt.supervisor.tick(NOW)
    await rt.supervisor.tick(NOW + timedelta(seconds=30))  # idempotent : pas de doublon
    assert await incidents(url) == [("screen.offline", "warning", "screen", False)]
    assert spy.names() == ["screen.status_changed", "incident.opened"]
    assert spy.events[0].payload["status"] == "offline"
    assert spy.events[0].payload["previous"] == "online"
    await sql(url, "UPDATE screen_device SET last_seen_at = :t", t=NOW + timedelta(minutes=1))
    await rt.supervisor.tick(NOW + timedelta(minutes=1, seconds=10))
    assert await incidents(url) == [("screen.offline", "warning", "screen", True)]
    assert spy.names().count("incident.resolved") == 1
    # une nouvelle panne ouvre un nouvel incident (le précédent est clos)
    await rt.supervisor.tick(NOW + timedelta(hours=1))
    assert len(await incidents(url)) == 2


async def test_scheduled_off_raises_no_incident(
    app: FastAPI,
    world: dict[str, Any],  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    await sql(
        world["url"],
        "INSERT INTO power_state (screen_id, desired, desired_reason, desired_since)"
        " VALUES (:s, 'off', 'plan', :t)",
        s=UUID(world["screen"]),
        t=NOW - timedelta(hours=3),
    )
    await rt.supervisor.tick(NOW)
    await rt.supervisor.tick(NOW + timedelta(hours=2))
    assert await incidents(world["url"]) == []


async def test_scheduled_off_closes_open_offline_incident(
    app: FastAPI,
    world: dict[str, Any],  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    await rt.supervisor.tick(NOW)
    assert len(await incidents(world["url"], "WHERE resolved_at IS NULL")) == 1
    await sql(
        world["url"],
        "INSERT INTO power_state (screen_id, desired, desired_reason, desired_since)"
        " VALUES (:s, 'off', 'plan', :t)",
        s=UUID(world["screen"]),
        t=NOW,
    )
    await rt.supervisor.tick(NOW + timedelta(seconds=30))
    assert await incidents(world["url"], "WHERE resolved_at IS NULL") == []


async def test_grace_after_power_on(app: FastAPI, world: dict[str, Any]) -> None:  # noqa: F811
    rt: Runtime = app.state.runtime
    await sql(
        world["url"],
        "INSERT INTO power_state (screen_id, desired, desired_reason, desired_since)"
        " VALUES (:s, 'on', 'plan', :t)",
        s=UUID(world["screen"]),
        t=NOW - timedelta(seconds=120),
    )
    await rt.supervisor.tick(NOW)
    assert await incidents(world["url"]) == []
    await rt.supervisor.tick(NOW + timedelta(seconds=600))
    assert len(await incidents(world["url"])) == 1


async def test_agent_offline_critical_then_revoked(
    app: FastAPI,
    world: dict[str, Any],  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    url = world["url"]
    await sql(
        url,
        "INSERT INTO site_agent (site_id, name, token_hash, token_prefix, last_seen_at)"
        " VALUES (:s, 'Pi', :h, 'vds_agt_', :t)",
        s=UUID(world["site"]),
        h=uuid4().hex,
        t=NOW - timedelta(seconds=301),
    )
    await rt.supervisor.tick(NOW)
    rows = await sql(
        url, "SELECT kind, severity, subject_type FROM incident WHERE kind LIKE 'agent%'"
    )
    assert rows == [("agent.offline", "critical", "agent")]
    await sql(url, "UPDATE site_agent SET revoked_at = :t", t=NOW)
    await rt.supervisor.tick(NOW + timedelta(seconds=30))
    assert await incidents(url, "WHERE kind = 'agent.offline' AND resolved_at IS NULL") == []


async def test_agent_recent_is_not_incident(
    app: FastAPI,
    world: dict[str, Any],  # noqa: F811
) -> None:
    await sql(
        world["url"],
        "INSERT INTO site_agent (site_id, name, token_hash, token_prefix, last_seen_at)"
        " VALUES (:s, 'Pi', :h, 'vds_agt_', :t)",
        s=UUID(world["site"]),
        h=uuid4().hex,
        t=NOW - timedelta(seconds=299),
    )
    await app.state.runtime.supervisor.tick(NOW)
    assert await incidents(world["url"], "WHERE kind = 'agent.offline'") == []


async def test_power_failed_resolved_by_verified_change(
    app: FastAPI,
    world: dict[str, Any],  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    url = world["url"]
    failed = Event(
        name="power.failed",
        payload={
            "screen_id": world["screen"],
            "state": "on",
            "attempted": ["power.wol"],
            "code": "power.timeout",
        },
    )
    await rt.bus.publish(failed)
    await rt.bus.publish(failed)  # un seul ouvert
    assert await incidents(url, "WHERE kind = 'power.failed'") == [
        ("power.failed", "warning", "screen", False)
    ]
    unverified = {"screen_id": world["screen"], "state": "on", "driver": "power.blank"}
    await rt.bus.publish(Event(name="power.changed", payload=unverified | {"verified": False}))
    assert (await incidents(url, "WHERE kind = 'power.failed'"))[0][3] is False
    await rt.bus.publish(Event(name="power.changed", payload=unverified | {"verified": True}))
    assert (await incidents(url, "WHERE kind = 'power.failed'"))[0][3] is True


async def test_module_quarantine_incident(app: FastAPI, world: dict[str, Any]) -> None:  # noqa: F811
    rt: Runtime = app.state.runtime
    await rt.bus.publish(
        Event(name="module.quarantined", payload={"module_id": "content.url", "code": "x"})
    )
    assert await incidents(world["url"], "WHERE kind = 'module.quarantined'") == [
        ("module.quarantined", "critical", "module", False)
    ]
    await rt.bus.publish(Event(name="module.enabled", payload={"module_id": "content.url"}))
    assert (await incidents(world["url"], "WHERE kind = 'module.quarantined'"))[0][3] is True


# --- API -----------------------------------------------------------------------------
async def test_incident_api_read_and_acknowledge(
    admin: AsyncClient,
    editor: AsyncClient,
    app: FastAPI,
    p: str,
    world: dict[str, Any],  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    await rt.supervisor.tick(NOW)
    url = f"{p}/api/v1/incidents"
    async with new_client(app) as nobody:
        assert (await nobody.get(url)).status_code == 401
    listed = (await editor.get(url)).json()
    assert len(listed) == 1
    inc = listed[0]
    assert inc["kind"] == "screen.offline" and inc["subject_name"] == "Hall"
    assert inc["site_name"] == "Moulins" and inc["acknowledged_at"] is None
    assert (await editor.get(f"{url}/{inc['id']}")).json()["id"] == inc["id"]
    assert (await admin.get(f"{url}?state=resolved")).json() == []
    assert len((await admin.get(f"{url}?kind=screen.offline&site_id={world['site']}")).json()) == 1
    assert (await admin.get(f"{url}?kind=agent.offline")).json() == []
    assert (await admin.get(f"{url}?limit=0")).status_code == 422
    # l'acquittement est réservé aux administrateurs, contrôle d'origine inclus
    ack = f"{url}/{inc['id']}/acknowledge"
    assert (await editor.post(ack, headers=SAME)).status_code == 403
    assert (await admin.post(ack, headers={"Origin": "https://evil.test"})).status_code == 403
    first = (await admin.post(ack, headers=SAME)).json()
    assert first["acknowledged_at"] and first["acknowledged_by"]
    again = (await admin.post(ack, headers=SAME)).json()
    assert again["acknowledged_at"] == first["acknowledged_at"]  # idempotent
    actions = await sql(world["url"], "SELECT action FROM audit_log WHERE action LIKE 'incident.%'")
    assert actions == [("incident.acknowledged",)]
    # introuvable, puis résolu
    missing = await admin.post(f"{url}/{uuid4()}/acknowledge", headers=SAME)
    assert (missing.status_code, missing.json()["code"]) == (404, "incidents.not_found")
    await sql(
        world["url"], "UPDATE screen_device SET last_seen_at = :t", t=NOW + timedelta(hours=1)
    )
    await rt.supervisor.tick(NOW + timedelta(hours=1))
    done = await admin.post(ack, headers=SAME)
    assert (done.status_code, done.json()["code"]) == (409, "incidents.already_resolved")
    assert len((await admin.get(f"{url}?state=all")).json()) == 1
    assert (await admin.get(url)).json() == []


# --- notify.mail de bout en bout ---------------------------------------------------------
@pytest.fixture(params=["starttls", "tls"])
def smtp(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    fakes = _fakes()
    s = fakes.FakeSmtp(mode=request.param).start()
    for k, v in s.env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VDS_PUBLIC_ORIGIN", "https://vds.test")
    yield s
    s.stop()


def trust(mod: Any, smtp: Any) -> None:
    ns: Any = sys.modules[type(mod).__module__]
    ns.tls_context = smtp.trusting_context  # autorité de test, la vérification reste active


def subject_of(msg: Any) -> str:
    return str(make_header(decode_header(msg["Subject"])))


async def test_notify_mail_one_mail_on_open_one_on_resolve(
    app: FastAPI,
    world: dict[str, Any],
    smtp: Any,
    base: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt: Runtime = app.state.runtime
    monkeypatch.setenv("VDS_BASE_PATH", base)
    await rt.registry.enable("notify.mail")
    try:
        mod: Any = rt.registry.instance("notify.mail")
        assert mod is not None
        trust(mod, smtp)
        assert (await mod.health()).status == "ok"
        await rt.supervisor.tick(NOW)  # écran hors ligne -> incident.opened -> mail
        await rt.supervisor.tick(NOW + timedelta(seconds=30))
        assert len(smtp.messages) == 1
        msg = smtp.messages[0]
        assert smtp.rcpts[0] == ["root@vds.test"]
        assert subject_of(msg) == "[VDS] Écran hors ligne : Hall"
        body = msg.get_payload(decode=True).decode()
        prefix = "" if base == "/" else base
        assert f"https://vds.test{prefix}/admin/screens/{world['screen']}" in body
        assert "Moulins" in body and "11:00 CET" in body  # 10:00 UTC, heure locale du site
        assert smtp.env()["VDS_SMTP_PASSWORD"] not in msg.as_string()
        # rejeu de l'événement (livraison au moins une fois) : aucun doublon
        opened = (await sql(world["url"], "SELECT id FROM incident"))[0][0]
        await rt.bus.publish(
            Event(
                name="incident.opened",
                payload={"incident_id": str(opened), "kind": "screen.offline"},
            )
        )
        assert len(smtp.messages) == 1
        # résolution : un seul second mail
        await sql(
            world["url"], "UPDATE screen_device SET last_seen_at = :t", t=NOW + timedelta(hours=1)
        )
        await rt.supervisor.tick(NOW + timedelta(hours=1))
        assert [subject_of(m) for m in smtp.messages][1:] == [
            "[VDS] Résolu - Écran hors ligne : Hall"
        ]
        rows = await sql(
            world["url"],
            "SELECT transition, status, attempts, code FROM m_notify_mail_delivery"
            " ORDER BY transition",
        )
        assert rows == [("opened", "sent", 1, None), ("resolved", "sent", 1, None)]
    finally:
        await rt.registry.disable("notify.mail")


async def test_notify_mail_retries_then_succeeds_and_kinds_filter(
    app: FastAPI,
    world: dict[str, Any],
    smtp: Any,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    await rt.registry.enable("notify.mail")
    try:
        mod: Any = rt.registry.instance("notify.mail")
        assert mod is not None
        trust(mod, smtp)
        smtp.reply = "451 Try again later"
        await rt.supervisor.tick(NOW)
        row = (
            await sql(world["url"], "SELECT status, attempts, code FROM m_notify_mail_delivery")
        )[0]
        assert row == ("pending", 1, "notify.mail.smtp_rejected")
        assert await mod.retry_due() == 0  # pas avant 1 minute
        smtp.reply = None
        await sql(world["url"], "UPDATE m_notify_mail_delivery SET next_attempt_at = now()")
        assert await mod.retry_due() == 1
        row = (await sql(world["url"], "SELECT status, attempts FROM m_notify_mail_delivery"))[0]
        assert row == ("sent", 2) and len(smtp.messages) == 1
        # un type d'incident non retenu ne produit aucun mail
        other = {"incident_id": str(uuid4()), "kind": "custom.other"}
        await rt.bus.publish(Event(name="incident.opened", payload=other))
        assert len(smtp.messages) == 1
    finally:
        await rt.registry.disable("notify.mail")


async def test_notify_mail_unconfigured_is_degraded_and_loses_nothing(
    app: FastAPI,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    monkeypatch.delenv("VDS_SMTP_HOST", raising=False)
    await rt.registry.enable("notify.mail")
    try:
        mod: Any = rt.registry.instance("notify.mail")
        assert mod is not None
        health = await mod.health()
        assert (health.status, health.code) == ("degraded", "notify.mail.not_configured")
        await rt.supervisor.tick(NOW)
        assert len(await incidents(world["url"])) == 1  # l'incident existe malgré tout
        assert await sql(world["url"], "SELECT 1 FROM m_notify_mail_delivery") == []
    finally:
        await rt.registry.disable("notify.mail")


async def test_notify_mail_password_not_in_module_api(
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    smtp: Any,  # noqa: F811
) -> None:
    rt: Runtime = app.state.runtime
    await rt.registry.enable("notify.mail")
    try:
        r = await admin.get(f"{p}/api/v1/config/organization/{await _org_id(rt)}/notify.mail")
        assert r.status_code == 200 and "smtp_password" in r.text
        assert smtp.env()["VDS_SMTP_PASSWORD"] not in r.text
    finally:
        await rt.registry.disable("notify.mail")


async def _org_id(rt: Runtime) -> str:
    async with rt.maker() as s:
        return str((await s.execute(text("SELECT id FROM organization"))).scalar_one())
