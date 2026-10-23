"""Appairage, jeton d'appareil, révocation, heartbeat, aux deux VDS_BASE_PATH."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import mk_screen

DEVICE = {"platform": "android", "model": "TCL", "app_version": "1.0.0", "screen_w": 1920}


@pytest.fixture(autouse=True)
async def _pairing_clean(migrated_db_url: str) -> AsyncIterator[None]:
    yield
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE pairing_request, screen_device CASCADE"))
    await engine.dispose()


async def sql(url: str, query: str) -> list[Any]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        result = await c.execute(text(query))
        rows = list(result.all()) if result.returns_rows else []
    await engine.dispose()
    return rows


async def start(c: AsyncClient, p: str) -> dict[str, Any]:
    r = await c.post(f"{p}/api/v1/player/pairings", json={"device": DEVICE})
    assert r.status_code == 201, r.text
    return dict(r.json())


def hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def poll_url(p: str, st: dict[str, Any]) -> str:
    return f"{p}/api/v1/player/pairings/{st['pairing_id']}"


async def pair(admin: AsyncClient, player: AsyncClient, p: str, screen: str) -> str:
    st = await start(player, p)
    r = await admin.post(
        f"{p}/api/v1/pairings/claim", json={"code": st["code"], "screen_id": screen}, headers=SAME
    )
    assert r.status_code == 200, r.text
    r = await player.get(poll_url(p, st), headers=hdr(st["poll_secret"]))
    assert r.status_code == 200, r.text
    return str(r.json()["device_token"])


async def test_full_flow(admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str) -> None:
    _, screen = await mk_screen(admin, p)
    events: list[str] = []

    async def on_event(event: Any) -> None:
        events.append(event.name)

    for name in ("screen.paired", "screen.unpaired"):
        app.state.runtime.bus.subscribe(name, on_event, subscriber="test")
    async with new_client(app) as player:
        st = await start(player, p)
        assert len(st["code"]) == 7 and st["code"][3] == "-" and st["poll_interval_s"] == 3
        url = poll_url(p, st)
        assert (await player.get(url, headers=hdr(st["poll_secret"]))).status_code == 202
        r = await admin.post(
            f"{p}/api/v1/pairings/claim",
            json={"code": st["code"].replace("-", "").lower(), "screen_id": screen},
            headers=SAME,
        )
        assert r.status_code == 200 and r.json() == {"screen_id": screen}
        r = await player.get(url, headers=hdr(st["poll_secret"]))
        assert r.status_code == 200
        token = r.json()["device_token"]
        assert token.startswith("vds_dev_") and r.json()["screen_id"] == screen
        # jeton remis une seule fois, jamais en clair en base
        assert (await player.get(url, headers=hdr(st["poll_secret"]))).status_code == 410
        dump = await sql(migrated_db_url, "SELECT token_hash FROM screen_device")
        assert len(dump) == 1 and token not in dump[0][0] and len(dump[0][0]) == 64
        hb = f"{p}/api/v1/player/heartbeat"
        r = await player.post(hb, json={"revision": "sha256:a", "uptime_s": 5}, headers=hdr(token))
        assert r.status_code == 200
        assert r.json()["commands"] == [] and r.json()["poll_interval_s"] == 60
        assert r.json()["revision"].startswith("sha256:") and r.json()["revision"] != "sha256:a"
        seen = await sql(migrated_db_url, "SELECT last_seen_at FROM screen_device")
        assert seen[0][0] is not None
        assert (await player.post(hb, json={}, headers=SAME)).status_code == 401
        bad = await player.post(hb, json={}, headers=hdr("vds_dev_x"))
        assert bad.status_code == 401 and bad.json()["code"] == "errors.device_unauthorized"
    actions = await sql(migrated_db_url, "SELECT action FROM audit_log WHERE action LIKE '%pair%'")
    assert {a[0] for a in actions} >= {"pairing.claimed", "screen.paired"}
    assert events == ["screen.paired"]


async def test_expired_and_invalid_code(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        await sql(
            migrated_db_url, "UPDATE pairing_request SET expires_at = now() - interval '1 minute'"
        )
        body = {"code": st["code"], "screen_id": screen}
        r = await admin.post(f"{p}/api/v1/pairings/claim", json=body, headers=SAME)
        assert r.status_code == 410 and r.json()["code"] == "errors.pairing_code_expired"
        r = await player.get(poll_url(p, st), headers=hdr(st["poll_secret"]))
        assert r.status_code == 410
    r = await admin.post(
        f"{p}/api/v1/pairings/claim", json={"code": "ZZZ-ZZZ", "screen_id": screen}, headers=SAME
    )
    assert r.status_code == 404 and r.json()["code"] == "errors.pairing_code_invalid"


async def test_claim_rules(editor: AsyncClient, admin: AsyncClient, app: FastAPI, p: str) -> None:
    _, screen = await mk_screen(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        body = {"code": st["code"], "screen_id": screen}
        r = await editor.post(f"{p}/api/v1/pairings/claim", json=body, headers=SAME)
        assert r.status_code == 403
        assert (await player.post(f"{p}/api/v1/pairings/claim", json=body)).status_code in (
            401,
            403,
        )
        wrong = await player.get(poll_url(p, st), headers=hdr("nope"))
        assert wrong.status_code == 401


async def test_revocation_and_replacement(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    reasons: list[str] = []

    async def on_event(event: Any) -> None:
        reasons.append(event.payload["reason"])

    app.state.runtime.bus.subscribe("screen.unpaired", on_event, subscriber="test")
    async with new_client(app) as player:
        t1 = await pair(admin, player, p, screen)
        t2 = await pair(admin, player, p, screen)  # remplace t1
        hb = f"{p}/api/v1/player/heartbeat"
        old = await player.post(hb, json={}, headers=hdr(t1))
        assert old.status_code == 401 and old.json()["code"] == "errors.device_revoked"
        assert (await player.post(hb, json={}, headers=hdr(t2))).status_code == 200
        r = await admin.delete(f"{p}/api/v1/screens/{screen}/device", headers=SAME)
        assert r.status_code == 204
        gone = await player.post(hb, json={}, headers=hdr(t2))
        assert gone.status_code == 401 and gone.json()["code"] == "errors.device_revoked"
        again = await admin.delete(f"{p}/api/v1/screens/{screen}/device", headers=SAME)
        assert again.status_code == 404
    assert reasons == ["replaced", "revoked"]
    active = await sql(
        migrated_db_url, "SELECT count(*) FROM screen_device WHERE revoked_at IS NULL"
    )
    assert active[0][0] == 0


async def test_rate_limiting(anon: AsyncClient, p: str) -> None:
    body = {"device": DEVICE}
    starts = [
        (await anon.post(f"{p}/api/v1/player/pairings", json=body)).status_code for _ in range(11)
    ]
    assert starts[:10] == [201] * 10 and starts[10] == 429
    pid = "00000000-0000-4000-8000-000000000001"
    polls = [
        (await anon.get(f"{p}/api/v1/player/pairings/{pid}", headers=hdr("x"))).status_code
        for _ in range(41)
    ]
    assert polls[0] == 404 and polls[-1] == 429
