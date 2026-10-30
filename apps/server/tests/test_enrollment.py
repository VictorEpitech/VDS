# ruff: noqa: F811
"""Enrôlement d'agent par code (E2) : création, échange public, limiteur, rotation, audit."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import connect, refused, sql
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from vds_sdk.agent.enroll import ENROLL_CODE_PREFIX

HOST = {
    "hostname": "pi-hall.local",
    "agent_version": "1.4.0",
    "platform": "linux/arm64",
}


@pytest.fixture(autouse=True)
async def _enrollment_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    await sql(migrated_db_url, "TRUNCATE remote_command, site_agent CASCADE")


async def mk_site(c: AsyncClient, p: str, slug: str = "moulins") -> str:
    r = await c.post(f"{p}/api/v1/sites", json={"name": slug, "slug": slug}, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def url(p: str, site: str, tail: str = "") -> str:
    return f"{p}/api/v1/sites/{site}/agent/enrollments{tail}"


async def issue(c: AsyncClient, p: str, site: str, **body: Any) -> dict[str, Any]:
    r = await c.post(url(p, site), json=body, headers=SAME)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    assert out["code"].startswith(ENROLL_CODE_PREFIX)
    return out


async def enroll(app: FastAPI, p: str, code: str, **over: Any) -> Any:
    async with new_client(app) as c:  # client sans cookie ni Origin : comme un agent
        return await c.post(f"{p}/api/v1/agent/enroll", json={"code": code, **HOST, **over})


async def test_create_returns_code_once_and_commands(
    admin: AsyncClient,
    p: str,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    assert out["server_url"] == "https://vds.test" + p
    assert out["code"] in out["commands"]["docker_run"]
    assert f"VDS_ENROLL_CODE={out['code']}" in out["files"]["agent_env"]
    assert out["commands"]["docker_login"] is None
    listed = (await admin.get(url(p, site))).json()
    assert [e["state"] for e in listed] == ["pending"]
    assert "code" not in listed[0] and out["code"] not in str(listed)
    rows = await sql(migrated_db_url, "SELECT code_sha256, channel FROM agent_enrollment")
    assert len(bytes(rows[0][0])) == 32 and rows[0][1] == "remote"
    dump = await sql(migrated_db_url, "SELECT row_to_json(t)::text FROM agent_enrollment t")
    dump += await sql(migrated_db_url, "SELECT details::text FROM audit_log")
    dump += await sql(migrated_db_url, "SELECT payload::text FROM event_outbox")
    assert out["code"] not in str(dump) and out["code"][8:] not in str(dump)


async def test_ttl_bounds(admin: AsyncClient, p: str) -> None:
    site = await mk_site(admin, p)
    for bad in (9, 1441):
        r = await admin.post(url(p, site), json={"ttl_min": bad}, headers=SAME)
        assert r.status_code == 422
    assert (await admin.post(url(p, site), json={"ttl_min": 10}, headers=SAME)).status_code == 201


async def test_public_origin_missing(admin: AsyncClient, p: str) -> None:
    site = await mk_site(admin, p)
    # sans VDS_PUBLIC_ORIGIN, pas de déduction depuis Host : 409 (couvert au niveau service)
    from vds_server.core.errors import ApiError
    from vds_server.enrollment.service import EnrollmentService
    from vds_server.settings import Settings

    svc = EnrollmentService(None, None, Settings(public_origin=None))  # type: ignore[arg-type]
    with pytest.raises(ApiError) as exc:
        svc.created_view(None, "x")  # type: ignore[arg-type]
    assert exc.value.code == "agent.public_origin_missing" and exc.value.status == 409
    assert site


async def test_enroll_creates_agent_then_replay_409(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    r = await enroll(app, p, out["code"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["site_id"] == site and body["token"].startswith("vds_agt_")
    assert body["ws_path"] == "ws/agent"
    r2 = await enroll(app, p, out["code"])
    assert (r2.status_code, r2.json()["code"]) == (409, "enroll.consumed")
    listed = (await admin.get(url(p, site))).json()[0]
    assert listed["state"] == "consumed" and listed["consumed_hostname"] == "pi-hall.local"
    assert listed["agent_id"] == body["agent_id"] and listed["consumed_ip"]
    # le jeton obtenu ouvre bien une session d'agent
    ws = await connect(app, p, body["token"])
    assert ws.accepted
    actions = await sql(migrated_db_url, "SELECT action FROM audit_log ORDER BY id")
    names = [a[0] for a in actions]
    assert "agent.enrollment.created" in names and "agent.enrollment.consumed" in names


async def test_unknown_expired_revoked(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    r = await enroll(app, p, ENROLL_CODE_PREFIX + "0" * 26)
    assert (r.status_code, r.json()["code"]) == (401, "enroll.invalid")
    out = await issue(admin, p, site)
    await sql(migrated_db_url, "UPDATE agent_enrollment SET expires_at = now() - interval '1 min'")
    r = await enroll(app, p, out["code"])
    assert (r.status_code, r.json()["code"]) == (410, "enroll.expired")
    assert (await admin.get(url(p, site))).json()[0]["state"] == "expired"
    out = await issue(admin, p, site)
    d = await admin.delete(url(p, site, f"/{out['id']}"), headers=SAME)
    assert d.status_code == 204
    r = await enroll(app, p, out["code"])  # révoquée : même réponse que inconnue
    assert (r.status_code, r.json()["code"]) == (401, "enroll.invalid")
    # idempotent, et 404 pour un identifiant d'un autre site
    assert (await admin.delete(url(p, site, f"/{out['id']}"), headers=SAME)).status_code == 204
    other = await mk_site(admin, p, "autre")
    assert (await admin.delete(url(p, other, f"/{out['id']}"), headers=SAME)).status_code == 404


async def test_revoke_consumed_409(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    assert (await enroll(app, p, out["code"])).status_code == 200
    d = await admin.delete(url(p, site, f"/{out['id']}"), headers=SAME)
    assert (d.status_code, d.json()["code"]) == (409, "enroll.consumed")


async def test_eleventh_failure_is_429(admin: AsyncClient, p: str, app: FastAPI) -> None:
    bad = ENROLL_CODE_PREFIX + "1" * 26
    for _ in range(10):
        assert (await enroll(app, p, bad)).status_code == 401
    r = await enroll(app, p, bad)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # même un bon code est refusé tant que l'IP est bloquée
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    assert (await enroll(app, p, out["code"])).status_code == 429


async def test_success_does_not_count_as_failure(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
) -> None:
    site = await mk_site(admin, p)
    for _ in range(3):
        out = await issue(admin, p, site)
        assert (await enroll(app, p, out["code"])).status_code == 200


async def test_origin_header_refused(admin: AsyncClient, p: str, app: FastAPI) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    async with new_client(app) as c:
        r = await c.post(
            f"{p}/api/v1/agent/enroll",
            json={"code": out["code"], **HOST},
            headers={"Origin": "https://vds.test"},
        )
    assert r.status_code == 403
    assert (await enroll(app, p, out["code"])).status_code == 200  # code intact


async def test_malformed_request_422(app: FastAPI, p: str) -> None:
    assert (await enroll(app, p, "vds_enr_court")).status_code == 422
    assert (await enroll(app, p, ENROLL_CODE_PREFIX + "0" * 26, hostname="a b")).status_code == 422
    assert (await enroll(app, p, ENROLL_CODE_PREFIX + "0" * 26, extra=1)).status_code == 422


async def test_editor_and_anonymous_forbidden(
    admin: AsyncClient,
    editor: AsyncClient,
    p: str,
    app: FastAPI,
) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    for method, tail in (("post", ""), ("get", ""), ("delete", f"/{out['id']}")):
        r = await getattr(editor, method)(url(p, site, tail), headers=SAME)
        assert r.status_code == 403, (method, r.text)
    async with new_client(app) as c:
        assert (await c.get(url(p, site))).status_code == 401
        assert (await c.post(url(p, site), json={}, headers=SAME)).status_code == 401


async def test_cookie_write_needs_same_origin(admin: AsyncClient, p: str) -> None:
    site = await mk_site(admin, p)
    r = await admin.post(url(p, site), json={}, headers={"Origin": "https://evil.test"})
    assert r.status_code == 403


async def test_new_code_supersedes_previous(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    first = await issue(admin, p, site)
    second = await issue(admin, p, site)
    states = {e["id"]: e["state"] for e in (await admin.get(url(p, site))).json()}
    assert states == {first["id"]: "revoked", second["id"]: "pending"}
    assert (await enroll(app, p, first["code"])).json()["code"] == "enroll.invalid"
    assert (await enroll(app, p, second["code"])).status_code == 200
    acts = await sql(
        migrated_db_url,
        "SELECT details->>'reason' FROM audit_log WHERE action='agent.enrollment.revoked'",
    )
    assert [a[0] for a in acts] == ["superseded"]


async def test_enrolled_event_is_critical_and_complete(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    body = (await enroll(app, p, out["code"])).json()
    rows = await sql(
        migrated_db_url,
        "SELECT payload FROM event_outbox WHERE name='agent.enrolled'",
    )
    assert len(rows) == 1
    payload = rows[0][0]
    assert payload["agent_id"] == body["agent_id"] and payload["enrollment_id"] == out["id"]
    assert payload["channel"] == "remote" and payload["replaced"] is False
    assert payload["moved_from_site_id"] is None and payload["hostname"] == "pi-hall.local"
    assert "code" not in payload and "token" not in payload


async def test_existing_agent_is_rotated_and_session_closed_4411(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
) -> None:
    site = await mk_site(admin, p)
    r = await admin.post(f"{p}/api/v1/sites/{site}/agents", json={"name": "old"}, headers=SAME)
    old_token, agent_id = r.json()["token"], r.json()["agent"]["id"]
    ws = await connect(app, p, old_token)
    await ws.hello()
    out = await issue(admin, p, site)
    body = (await enroll(app, p, out["code"])).json()
    assert body["agent_id"] == agent_id and body["token"] != old_token  # même agent, jeton neuf
    assert await ws.recv() is None and ws.close_code == 4411
    assert await refused(app, p, old_token) == 4401
    ws2 = await connect(app, p, body["token"])
    await ws2.hello()
    assert len((await admin.get(f"{p}/api/v1/sites/{site}/agents")).json()) == 1


async def test_previous_token_moves_agent_to_other_site(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
) -> None:
    a = await mk_site(admin, p, "a")
    b = await mk_site(admin, p, "b")
    r = await admin.post(f"{p}/api/v1/sites/{a}/agents", json={"name": "x"}, headers=SAME)
    old_token = r.json()["token"]
    ws = await connect(app, p, old_token)
    await ws.hello()
    out = await issue(admin, p, b)
    resp = await enroll(app, p, out["code"], previous_token=old_token)
    assert resp.status_code == 200 and resp.json()["site_id"] == b
    assert await ws.recv() is None and ws.close_code == 4410  # ancien agent révoqué
    assert await refused(app, p, old_token) == 4410
    agents_a = (await admin.get(f"{p}/api/v1/sites/{a}/agents")).json()
    assert [x["status"] for x in agents_a] == ["revoked"]


async def test_previous_token_unknown_or_same_site_is_ignored(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
) -> None:
    a = await mk_site(admin, p, "a")
    out = await issue(admin, p, a)
    r = await enroll(app, p, out["code"], previous_token="vds_agt_inconnu")  # noqa: S106
    assert r.status_code == 200


async def test_no_code_in_logs(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    await enroll(app, p, out["code"])
    await enroll(app, p, out["code"])
    assert out["code"] not in caplog.text and out["code"][8:] not in caplog.text


async def test_archived_site_refused(
    admin: AsyncClient,
    p: str,
    app: FastAPI,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    out = await issue(admin, p, site)
    await sql(migrated_db_url, "UPDATE site SET archived_at = now()")
    r = await enroll(app, p, out["code"])
    assert (r.status_code, r.json()["code"]) == (409, "site.archived")


async def test_purge_of_old_expired(
    admin: AsyncClient,
    p: str,
    migrated_db_url: str,
) -> None:
    site = await mk_site(admin, p)
    await issue(admin, p, site)
    await sql(migrated_db_url, "UPDATE agent_enrollment SET expires_at = now() - interval '31 d'")
    await issue(admin, p, site)
    assert (await sql(migrated_db_url, "SELECT count(*) FROM agent_enrollment"))[0][0] == 1


def test_migration_0008_reversible(migrated_db_url: str, alembic_cfg: Config) -> None:
    command.downgrade(alembic_cfg, "0007_drop_provision_ack")
    command.upgrade(alembic_cfg, "heads")
