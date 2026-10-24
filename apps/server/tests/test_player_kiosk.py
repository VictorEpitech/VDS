"""Kiosque géré (K1) : réglage hérité, maintenance, programmation, heartbeat, audit."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _content_types, _diffusion_clean, mk_screen  # noqa: F401
from test_pairing import _pairing_clean, hdr, pair, sql  # noqa: F401
from vds_server.player.kiosk import KioskRequest, format_until, served

NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)


def test_served_is_pure() -> None:
    soon = NOW + timedelta(minutes=5)
    assert served(KioskRequest(), NOW) == {"mode": "locked", "until": None}
    assert served(KioskRequest("unlocked"), NOW) == {"mode": "unlocked", "until": None}
    got = served(KioskRequest("locked", soon), NOW)
    assert got == {"mode": "maintenance", "until": "2026-10-24T10:05:00Z"}
    assert served(KioskRequest("unlocked", soon), NOW)["mode"] == "maintenance"
    # échéance atteinte ou passée : on retombe sur le mode configuré
    assert served(KioskRequest("locked", NOW), NOW) == {"mode": "locked", "until": None}
    past = NOW - timedelta(seconds=1)
    assert served(KioskRequest("unlocked", past), NOW) == {"mode": "unlocked", "until": None}
    assert format_until(NOW) == "2026-10-24T10:00:00Z"


async def mk_group(c: AsyncClient, p: str, site: str) -> str:
    r = await c.post(f"{p}/api/v1/sites/{site}/groups", json={"name": "G"}, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def doc(player: AsyncClient, p: str, token: str) -> dict[str, object]:
    r = await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_config_scopes_and_read_only(admin: AsyncClient, p: str) -> None:
    site, screen = await mk_screen(admin, p)
    group = await mk_group(admin, p, site)
    await admin.patch(f"{p}/api/v1/screens/{screen}", json={"group_id": group}, headers=SAME)
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    key = "core/player.kiosk"
    for level, tid in (("organization", org), ("site", site)):
        r = await admin.put(
            f"{p}/api/v1/config/{level}/{tid}/{key}", json={"value": "unlocked"}, headers=SAME
        )
        assert r.status_code == 422 and r.json()["code"] == "config.scope_not_allowed"
    bad = await admin.put(
        f"{p}/api/v1/config/screen/{screen}/{key}", json={"value": "open"}, headers=SAME
    )
    assert bad.status_code == 422 and bad.json()["code"] == "config.value_invalid"
    ok = await admin.put(
        f"{p}/api/v1/config/group/{group}/{key}", json={"value": "unlocked"}, headers=SAME
    )
    assert ok.status_code == 204
    cfg = (await admin.get(f"{p}/api/v1/config/screen/{screen}/core")).json()["values"]
    assert cfg["player.kiosk"]["value"] == "unlocked"
    assert cfg["player.kiosk"]["origin"]["level"] == "group"
    default = cfg["player.kiosk_maintenance_until"]
    assert default["value"] is None and default["origin"]["level"] == "default"
    url = f"{p}/api/v1/config/screen/{screen}/core/player.kiosk_maintenance_until"
    put = await admin.put(url, json={"value": "2030-01-01T00:00:00Z"}, headers=SAME)
    assert put.status_code == 422 and put.json()["code"] == "config.read_only"
    dele = await admin.delete(url, headers=SAME)
    assert dele.status_code == 422 and dele.json()["code"] == "config.read_only"


async def test_schedule_kiosk_inheritance_and_maintenance(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site, screen = await mk_screen(admin, p)
    r = await admin.post(f"{p}/api/v1/sites/{site}/groups", json={"name": "G"}, headers=SAME)
    group = r.json()["id"]
    await admin.patch(f"{p}/api/v1/screens/{screen}", json={"group_id": group}, headers=SAME)
    url = f"{p}/api/v1/player/schedule"
    cfg = f"{p}/api/v1/config"
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        base_doc = await doc(player, p, token)
        assert base_doc["kiosk"] == {"mode": "locked", "until": None}
        etag0 = f'"{base_doc["revision"]}"'

        await admin.put(
            f"{cfg}/group/{group}/core/player.kiosk", json={"value": "unlocked"}, headers=SAME
        )
        d1 = await doc(player, p, token)
        assert d1["kiosk"] == {"mode": "unlocked", "until": None}
        assert d1["revision"] != base_doc["revision"]
        h = {**hdr(token), "If-None-Match": etag0}
        assert (await player.get(url, headers=h)).status_code == 200  # ETag périmé

        await admin.put(
            f"{cfg}/screen/{screen}/core/player.kiosk", json={"value": "locked"}, headers=SAME
        )
        assert (await doc(player, p, token))["kiosk"]["mode"] == "locked"  # type: ignore[index]

        # maintenance
        mu = f"{p}/api/v1/screens/{screen}/kiosk/maintenance"
        for bad in (4, 241, 0):
            r = await admin.post(mu, json={"duration_min": bad}, headers=SAME)
            assert r.status_code == 422
        r = await admin.post(mu, json={"duration_min": 30, "reason": "mise à jour"}, headers=SAME)
        assert r.status_code == 200, r.text
        until = datetime.fromisoformat(r.json()["until"])
        assert timedelta(minutes=29) < until - datetime.now(UTC) <= timedelta(minutes=30)
        d2 = await doc(player, p, token)
        k = d2["kiosk"]
        assert isinstance(k, dict) and k["mode"] == "maintenance"
        assert parse(k["until"]) == until
        assert (
            await player.get(url, headers={**hdr(token), "If-None-Match": f'"{d2["revision"]}"'})
        ).status_code == 304

        # config générique : échéance en lecture, origine screen
        v = (await admin.get(f"{cfg}/screen/{screen}/core")).json()["values"]
        mv = v["player.kiosk_maintenance_until"]
        assert parse(mv["value"]) == until and mv["origin"]["level"] == "screen"

        # ScreenOut + heartbeat
        out = (await admin.get(f"{p}/api/v1/screens/{screen}")).json()["kiosk"]
        assert out["requested"] == "maintenance" and parse(out["until"]) == until
        assert out["applied"] is None and out["enforcement"] is None
        hb = await player.post(
            f"{p}/api/v1/player/heartbeat",
            json={"kiosk": {"mode": "maintenance", "enforcement": "soft"}},
            headers=hdr(token),
        )
        assert hb.status_code == 200
        out = (await admin.get(f"{p}/api/v1/screens/{screen}")).json()["kiosk"]
        assert out["applied"] == "maintenance" and out["enforcement"] == "soft"
        bad_hb = await player.post(
            f"{p}/api/v1/player/heartbeat",
            json={"kiosk": {"mode": "open", "enforcement": "soft"}},
            headers=hdr(token),
        )
        assert bad_hb.status_code == 422

        # fin anticipée, idempotente
        assert (await admin.delete(mu, headers=SAME)).status_code == 204
        assert (await admin.delete(mu, headers=SAME)).status_code == 204
        assert (await doc(player, p, token))["kiosk"] == {"mode": "locked", "until": None}

    actions = [
        row[0]
        for row in await sql(
            migrated_db_url,
            "SELECT action FROM audit_log WHERE action LIKE 'screen.kiosk.%' ORDER BY id",
        )
    ]
    assert actions == [
        "screen.kiosk.unlocked",
        "screen.kiosk.locked",
        "screen.kiosk.maintenance_started",
        "screen.kiosk.maintenance_ended",
    ]


def parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


async def test_maintenance_expiry_detected_once(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    past = format_until(datetime.now(UTC) - timedelta(minutes=1))
    insert = (
        "INSERT INTO config_override (screen_id, namespace, key, value) VALUES "  # noqa: S608
        f"('{screen}', 'core', 'player.kiosk_maintenance_until', '\"{past}\"'::jsonb)"
    )
    await sql(migrated_db_url, insert)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        for _ in range(2):
            assert (await doc(player, p, token))["kiosk"] == {"mode": "locked", "until": None}
    rows = await sql(
        migrated_db_url,
        "SELECT actor_kind, details->>'reason' FROM audit_log "
        "WHERE action = 'screen.kiosk.maintenance_ended'",
    )
    assert [tuple(r) for r in rows] == [("system", "expired")]
    left = await sql(
        migrated_db_url,
        "SELECT 1 FROM config_override WHERE key = 'player.kiosk_maintenance_until'",
    )
    assert left == []


async def test_maintenance_access(
    admin: AsyncClient, editor: AsyncClient, app: FastAPI, p: str
) -> None:
    _, screen = await mk_screen(admin, p)
    mu = f"{p}/api/v1/screens/{screen}/kiosk/maintenance"
    body = {"duration_min": 10}
    async with new_client(app) as nobody:
        assert (await nobody.post(mu, json=body, headers=SAME)).status_code == 401
    assert (await editor.post(mu, json=body, headers=SAME)).status_code == 403
    assert (await editor.delete(mu, headers=SAME)).status_code == 403
    cross = await admin.post(mu, json=body, headers={"Origin": "https://evil.example"})
    assert cross.status_code == 403
    missing = f"{p}/api/v1/screens/{UUID(int=1)}/kiosk/maintenance"
    r = await admin.post(missing, json=body, headers=SAME)
    assert r.status_code == 404 and r.json()["code"] == "errors.screen_not_found"
