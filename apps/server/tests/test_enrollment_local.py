# ruff: noqa: F811, ASYNC240
"""Canal local d'enrôlement (E3) : dépôt atomique 0600, présence bornée, 409 propres."""

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import sql
from test_auth import SAME, admin, anon, app, base, clean, editor, p  # noqa: F401
from test_enrollment import HOST, enroll, mk_site, url
from vds_sdk.agent.enroll import ENROLL_CODE_PREFIX


@pytest.fixture
def local_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "local-agent"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("VDS_LOCAL_AGENT_DIR", str(d))
    yield d


def presence(local_dir: Path, age_s: float = 0, **over: Any) -> None:
    body: dict[str, Any] = {
        "v": 1,
        "hostname": "srv",
        "agent_version": "1.4.0",
        "platform": "linux/amd64",
        "state": "dormant",
        "ts": (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat(),
    }
    (local_dir / "presence.json").write_text(json.dumps(body | over))


LOCAL = "/api/v1/agent/local"


def activate_url(p: str, site: str) -> str:
    return f"{p}/api/v1/sites/{site}/agent/local"


async def test_unconfigured_is_unavailable(admin: AsyncClient, p: str) -> None:
    r = await admin.get(f"{p}{LOCAL}")
    assert r.status_code == 200
    assert r.json() == {"available": False, "detected": None, "site_id": None}
    site = await mk_site(admin, p)
    r = await admin.post(activate_url(p, site), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "agent.local.unavailable")


async def test_configured_but_not_detected(local_dir: Path, admin: AsyncClient, p: str) -> None:
    r = await admin.get(f"{p}{LOCAL}")
    assert r.json() == {"available": True, "detected": None, "site_id": None}
    site = await mk_site(admin, p)
    r = await admin.post(activate_url(p, site), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "agent.local.not_detected")
    assert list(local_dir.iterdir()) == []  # rien déposé
    assert (await admin.get(url(p, site))).json() == []  # aucun code créé


async def test_stale_presence_is_not_detected(local_dir: Path, admin: AsyncClient, p: str) -> None:
    presence(local_dir, age_s=31)
    assert (await admin.get(f"{p}{LOCAL}")).json()["detected"] is None
    site = await mk_site(admin, p)
    r = await admin.post(activate_url(p, site), headers=SAME)
    assert (r.status_code, r.json()["code"]) == (409, "agent.local.not_detected")
    presence(local_dir, age_s=5)
    detected = (await admin.get(f"{p}{LOCAL}")).json()["detected"]
    assert detected["hostname"] == "srv" and detected["state"] == "dormant"


async def test_symlink_and_oversized_presence_ignored(
    local_dir: Path, admin: AsyncClient, p: str, tmp_path: Path
) -> None:
    presence(tmp_path)  # présence valide, mais hors du dossier
    (local_dir / "presence.json").symlink_to(tmp_path / "presence.json")
    assert (await admin.get(f"{p}{LOCAL}")).json()["detected"] is None
    (local_dir / "presence.json").unlink()
    presence(local_dir, error="e" * 100, pad="x" * 5000)
    assert (local_dir / "presence.json").stat().st_size > 4096
    assert (await admin.get(f"{p}{LOCAL}")).json()["detected"] is None
    (local_dir / "presence.json").write_text("pas du json")
    assert (await admin.get(f"{p}{LOCAL}")).json()["detected"] is None
    (local_dir / "presence.json").unlink()
    (local_dir / "presence.json").mkdir()  # pas un fichier régulier
    assert (await admin.get(f"{p}{LOCAL}")).json()["detected"] is None


async def test_activation_writes_handoff_matching_db(
    local_dir: Path, admin: AsyncClient, p: str, app: FastAPI, migrated_db_url: str
) -> None:
    presence(local_dir)
    site = await mk_site(admin, p)
    r = await admin.post(activate_url(p, site), headers=SAME)
    assert r.status_code == 202, r.text
    out = r.json()
    f = local_dir / "enroll.json"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert [x.name for x in local_dir.iterdir() if x.name != "presence.json"] == ["enroll.json"]
    data = json.loads(f.read_text())
    assert data["v"] == 1 and data["enrollment_id"] == out["enrollment_id"]
    assert data["code"].startswith(ENROLL_CODE_PREFIX)
    rows = await sql(
        migrated_db_url, "SELECT code_sha256, channel, expires_at, created_by FROM agent_enrollment"
    )
    assert bytes(rows[0][0]) == hashlib.sha256(data["code"].encode()).digest()
    assert rows[0][1] == "local" and rows[0][3] is not None
    ttl = rows[0][2] - datetime.now(UTC)
    assert timedelta(minutes=9) < ttl <= timedelta(minutes=10)
    # le serveur n'a jamais exposé le code à l'admin
    assert data["code"] not in r.text
    listed = (await admin.get(url(p, site))).json()[0]
    assert listed["channel"] == "local" and listed["state"] == "pending"
    # l'agent échange le code déposé : même mécanisme que le distant
    resp = await enroll(app, p, data["code"], **{**HOST, "hostname": "srv"})
    assert resp.status_code == 200 and resp.json()["site_id"] == site
    acts = [a[0] for a in await sql(migrated_db_url, "SELECT action FROM audit_log ORDER BY id")]
    assert "agent.local.activated" in acts and "agent.enrollment.consumed" in acts
    # la détection associe ensuite l'agent à son site
    presence(local_dir, state="active", agent_id=resp.json()["agent_id"])
    got = (await admin.get(f"{p}{LOCAL}")).json()
    assert got["site_id"] == site and got["detected"]["state"] == "active"


async def test_second_activation_replaces_file_and_supersedes(
    local_dir: Path, admin: AsyncClient, p: str
) -> None:
    presence(local_dir)
    a = await mk_site(admin, p, "a")
    b = await mk_site(admin, p, "b")
    first = (await admin.post(activate_url(p, a), headers=SAME)).json()
    code1 = json.loads((local_dir / "enroll.json").read_text())["code"]
    second = (await admin.post(activate_url(p, b), headers=SAME)).json()
    code2 = json.loads((local_dir / "enroll.json").read_text())["code"]
    assert code1 != code2 and first["enrollment_id"] != second["enrollment_id"]
    assert sorted(x.name for x in local_dir.iterdir()) == ["enroll.json", "presence.json"]
    # l'inscription du site a est encore valable (autre site) : une par site seulement
    assert (await admin.get(url(p, a))).json()[0]["state"] == "pending"
    again = (await admin.post(activate_url(p, b), headers=SAME)).json()
    states = {e["id"]: e["state"] for e in (await admin.get(url(p, b))).json()}
    assert states[second["enrollment_id"]] == "revoked" and states[again["enrollment_id"]] == (
        "pending"
    )


async def test_activation_permissions(
    local_dir: Path, admin: AsyncClient, editor: AsyncClient, p: str, app: FastAPI
) -> None:
    presence(local_dir)
    site = await mk_site(admin, p)
    assert (await editor.post(activate_url(p, site), headers=SAME)).status_code == 403
    assert (await editor.get(f"{p}{LOCAL}")).status_code == 403
    r = await admin.post(activate_url(p, site), headers={"Origin": "https://evil.test"})
    assert r.status_code == 403
    assert not (local_dir / "enroll.json").exists()
    from test_auth import new_client

    async with new_client(app) as c:
        assert (await c.get(f"{p}{LOCAL}")).status_code == 401
        assert (await c.post(activate_url(p, site), headers=SAME)).status_code == 401


async def test_unknown_site_404_and_nothing_written(
    local_dir: Path, admin: AsyncClient, p: str
) -> None:
    presence(local_dir)
    r = await admin.post(activate_url(p, str(uuid4())), headers=SAME)
    assert r.status_code == 404
    assert not (local_dir / "enroll.json").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignore les droits de dossier")
async def test_write_failure_revokes_code(local_dir: Path, admin: AsyncClient, p: str) -> None:
    presence(local_dir)
    site = await mk_site(admin, p)
    (local_dir / "presence.json").chmod(0o644)
    local_dir.chmod(0o500)  # lecture seule : le dépôt échoue
    try:
        r = await admin.post(activate_url(p, site), headers=SAME)
    finally:
        local_dir.chmod(0o700)
    assert (r.status_code, r.json()["code"]) == (502, "agent.local.write_failed")
    assert [e["state"] for e in (await admin.get(url(p, site))).json()] == ["revoked"]
