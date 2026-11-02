"""POST /screens/pair : création + claim atomiques, aux deux VDS_BASE_PATH."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import mk_screen  # noqa: F401
from test_pairing import DEVICE, _pairing_clean, hdr, poll_url, sql, start  # noqa: F401


async def site_of(c: AsyncClient, p: str) -> str:
    r = await c.post(f"{p}/api/v1/sites", json={"name": "Moulins", "slug": "moulins"}, headers=SAME)
    return str(r.json()["id"])


async def pair(c: AsyncClient, p: str, code: str, site: str, **extra: Any) -> Any:
    body = {"code": code, "site_id": site, "name": "Accueil", **extra}
    return await c.post(f"{p}/api/v1/screens/pair", json=body, headers=SAME)


async def count(url: str) -> int:
    return int((await sql(url, "SELECT count(*) FROM screen"))[0][0])


async def test_success_then_device_token(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        r = await pair(admin, p, st["code"].lower(), site)
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["replayed"] is False and j["pairing"]["status"] == "claimed"
        assert j["screen"]["device"] is None and j["screen"]["orientation"] == "landscape"
        got = await player.get(poll_url(p, st), headers=hdr(st["poll_secret"]))
        assert got.status_code == 200 and got.json()["screen_id"] == j["screen"]["id"]
    acts = await sql(migrated_db_url, "SELECT action, details FROM audit_log")
    names = {a[0] for a in acts}
    assert {"screen.created", "pairing.claimed"} <= names
    created = [a for a in acts if a[0] == "screen.created"][0][1]
    assert created["via"] == "screens.pair"


async def test_orientation_derived(admin: AsyncClient, app: FastAPI, p: str) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        r = await player.post(
            f"{p}/api/v1/player/pairings",
            json={"device": {**DEVICE, "screen_w": 1080, "screen_h": 1920}},
        )
        code = r.json()["code"]
        r = await pair(admin, p, code, site)
        assert r.json()["screen"]["orientation"] == "portrait"


async def test_invalid_expired_claimed(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p)
    r = await pair(admin, p, "ZZZ-ZZZ", site)
    assert r.status_code == 404 and r.json()["code"] == "errors.pairing_code_invalid"
    async with new_client(app) as player:
        st = await start(player, p)
        await sql(
            migrated_db_url, "UPDATE pairing_request SET expires_at = now() - interval '1 minute'"
        )
        r = await pair(admin, p, st["code"], site)
        assert r.status_code == 410 and r.json()["code"] == "errors.pairing_code_expired"
        status = await sql(migrated_db_url, "SELECT status FROM pairing_request")
        assert status[0][0] == "expired"
    assert await count(migrated_db_url) == 0


async def test_replay_same_admin_and_other_admin(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        first = await pair(admin, p, st["code"], site)
        again = await pair(admin, p, st["code"], site)
        assert again.status_code == 200 and again.json()["replayed"] is True
        assert again.json()["screen"]["id"] == first.json()["screen"]["id"]
        # consommé par le player : le rejeu reste possible
        await player.get(poll_url(p, st), headers=hdr(st["poll_secret"]))
        late = await pair(admin, p, st["code"], site)
        assert late.status_code == 200 and late.json()["pairing"]["status"] == "consumed"
        assert await count(migrated_db_url) == 1
        # autre admin (claimed_by différent)
        await sql(migrated_db_url, "UPDATE pairing_request SET claimed_by = NULL")
        r = await pair(admin, p, st["code"], site)
        assert r.status_code == 409 and r.json()["code"] == "errors.pairing_code_claimed"
        assert await count(migrated_db_url) == 1


async def test_claim_on_claimed_code_is_409(admin: AsyncClient, app: FastAPI, p: str) -> None:
    _, screen = await mk_screen(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        body = {"code": st["code"], "screen_id": screen}
        assert (
            await admin.post(f"{p}/api/v1/pairings/claim", json=body, headers=SAME)
        ).status_code == 200
        r = await admin.post(f"{p}/api/v1/pairings/claim", json=body, headers=SAME)
        assert r.status_code == 409 and r.json()["code"] == "errors.pairing_code_claimed"


async def test_access_rules(
    editor: AsyncClient, admin: AsyncClient, anon: AsyncClient, app: FastAPI, p: str
) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        assert (await pair(editor, p, st["code"], site)).status_code == 403
        assert (await anon.post(f"{p}/api/v1/screens/pair", json={})).status_code in (401, 403)
        body = {"code": st["code"], "site_id": site, "name": "X"}
        r = await admin.post(
            f"{p}/api/v1/screens/pair", json=body, headers={"Origin": "https://evil.test"}
        )
        assert r.status_code == 403
        assert (await pair(admin, p, st["code"], site, bogus=1)).status_code == 422


async def test_atomic_no_orphan_screen(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        # groupe d'un autre site : échec après lock_code, avant toute création
        other = await admin.post(
            f"{p}/api/v1/sites", json={"name": "Autre", "slug": "autre"}, headers=SAME
        )
        grp = await admin.post(
            f"{p}/api/v1/sites/{other.json()['id']}/groups", json={"name": "G"}, headers=SAME
        )
        r = await pair(admin, p, st["code"], site, group_id=grp.json()["id"])
        assert r.status_code == 422 and r.json()["code"] == "screen.group_other_site"
        assert await count(migrated_db_url) == 0
        # échec lors de mark_claimed : rollback complet de l'écran
        from vds_server.player.service import PairingService

        orig = PairingService.mark_claimed

        def boom(self: Any, *a: Any, **k: Any) -> None:
            raise RuntimeError("boom")

        PairingService.mark_claimed = boom  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError):
                await pair(admin, p, st["code"], site)
        finally:
            PairingService.mark_claimed = orig  # type: ignore[method-assign]
        assert await count(migrated_db_url) == 0
        status = await sql(migrated_db_url, "SELECT status FROM pairing_request")
        assert status[0][0] == "pending"
        ok = await pair(admin, p, st["code"], site)
        assert ok.status_code == 201


async def test_shared_rate_limit(admin: AsyncClient, p: str) -> None:
    site = await site_of(admin, p)
    codes = []
    for i in range(31):
        r = await pair(admin, p, f"ZZ{i:04d}"[:6], site)
        codes.append(r.status_code)
    assert codes[:30] == [404] * 30 and codes[30] == 429


async def test_validation_and_unknown_site(admin: AsyncClient, app: FastAPI, p: str) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        r = await pair(admin, p, st["code"], "00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404 and r.json()["code"] == "site.not_found"
        assert (await pair(admin, p, "AB", site)).status_code == 422
        assert (await pair(admin, p, "A" * 13, site)).status_code == 422
        assert (await pair(admin, p, st["code"], site, orientation="diagonal")).status_code == 422
        assert (await pair(admin, p, st["code"], site, name="")).status_code == 422
        # toujours réclamable après ces échecs
        ok = await pair(admin, p, st["code"], site, orientation="portrait")
        assert ok.status_code == 201 and ok.json()["screen"]["orientation"] == "portrait"


async def test_replay_refused_when_screen_archived(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p)
    async with new_client(app) as player:
        st = await start(player, p)
        assert (await pair(admin, p, st["code"], site)).status_code == 201
        await sql(migrated_db_url, "UPDATE screen SET archived_at = now()")
        r = await pair(admin, p, st["code"], site)
        assert r.status_code == 409 and r.json()["code"] == "errors.pairing_code_claimed"
        assert await count(migrated_db_url) == 1


async def test_audit_failures_and_replay_mismatch(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site = await site_of(admin, p)
    assert (await pair(admin, p, "ZZZ-ZZZ", site)).status_code == 404
    async with new_client(app) as player:
        st = await start(player, p)
        assert (await pair(admin, p, st["code"], site)).status_code == 201
        ok = await pair(admin, p, st["code"], site)
        assert ok.status_code == 200
        bad = await pair(admin, p, st["code"], site, name="Autre")
        assert bad.status_code == 409
        rows = await sql(migrated_db_url, "SELECT action, details::text FROM audit_log")
        actions = [r[0] for r in rows]
        assert actions.count("pairing.claim_failed") == 2
        assert actions.count("pairing.replayed") == 1
        assert all("ZZZ" not in r[1] and st["code"] not in r[1] for r in rows)
        assert await count(migrated_db_url) == 1
