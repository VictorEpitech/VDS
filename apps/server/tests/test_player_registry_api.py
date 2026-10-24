"""Registre des content-types pour l'admin et état d'appareil dans ScreenOut (deux base paths)."""

from fastapi import FastAPI
from httpx import AsyncClient
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import mk_screen
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401


async def test_content_types_registry(admin: AsyncClient, editor: AsyncClient, p: str) -> None:
    for c in (admin, editor):
        r = await c.get(f"{p}/api/v1/registry/content-types")
        assert r.status_code == 200, r.text
        by_id = {t["id"]: t for t in r.json()}
        assert {"content.image", "content.video", "content.pdf", "content.url"} <= by_id.keys()
        img = by_id["content.image"]
        assert img["element"] == "vds-content-image"
        assert img["item_schema"]["required"] == ["media"]
        assert img["config_schema"] is not None
        assert set(img["i18n"]) == {"fr", "en"}
        assert img["i18n"]["fr"]["item"]["content_image"]["media"]["title"]
        assert "image/png" in img["accepts"] and img["name"]["fr"]


async def test_module_settings_schema(admin: AsyncClient, editor: AsyncClient, p: str) -> None:
    for c in (admin, editor):
        r = await c.get(f"{p}/api/v1/registry/modules/notify.mail/settings")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["config_schema"]["properties"]["notify_admins"]["x-vds-level"] == "simple"
        assert set(body["i18n"]) == {"fr", "en"}
    for mid in ("content.image", "inconnu"):
        r = await admin.get(f"{p}/api/v1/registry/modules/{mid}/settings")
        assert r.status_code == 404


async def test_power_driver_list(admin: AsyncClient, editor: AsyncClient, p: str) -> None:
    for c in (admin, editor):
        r = await c.get(f"{p}/api/v1/registry/modules", params={"kind": "power-driver"})
        assert r.status_code == 200, r.text
        by_id = {m["id"]: m for m in r.json()}
        assert {"power.wol", "power.samsung-ws", "power.adb", "power.blank"} <= by_id.keys()
        assert all(i.startswith("power.") for i in by_id)
        wol = by_id["power.wol"]
        assert wol["has_settings"] is True and wol["enabled"] is True and wol["name"]["fr"]
        assert by_id["power.blank"]["has_settings"] is False
    r = await admin.get(f"{p}/api/v1/registry/modules", params={"kind": "bogus"})
    assert r.status_code == 422


async def test_power_driver_list_requires_auth(app: FastAPI, p: str) -> None:
    async with new_client(app) as anon_client:
        r = await anon_client.get(f"{p}/api/v1/registry/modules", params={"kind": "power-driver"})
        assert r.status_code == 401


async def test_content_types_requires_auth(app: FastAPI, p: str) -> None:
    async with new_client(app) as anon_client:
        r = await anon_client.get(f"{p}/api/v1/registry/content-types")
        assert r.status_code == 401


async def test_screen_device_state(admin: AsyncClient, app: FastAPI, p: str) -> None:
    _, screen = await mk_screen(admin, p)
    r = await admin.get(f"{p}/api/v1/screens/{screen}")
    assert r.json()["device"] is None
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        dev = (await admin.get(f"{p}/api/v1/screens/{screen}")).json()["device"]
        assert dev["presence"] == "unseen" and dev["platform"] == "android"
        assert dev["heartbeat"] is None and dev["revoked_at"] is None
        assert "token" not in str(dev)
        hb = {"revision": "sha256:abc", "uptime_s": 5, "cache": {"missing": ["a", "b"]}}
        r = await player.post(f"{p}/api/v1/player/heartbeat", json=hb, headers=hdr(token))
        assert r.status_code == 200
        listed = (await admin.get(f"{p}/api/v1/screens")).json()[0]["device"]
        assert listed["presence"] == "online" and listed["last_seen_at"]
        assert listed["heartbeat"] == {
            "revision": "sha256:abc",
            "stale": False,
            "uptime_s": 5,
            "missing_assets": 2,
        }
        r = await admin.delete(f"{p}/api/v1/screens/{screen}/device", headers=SAME)
        assert r.status_code == 204
        dev = (await admin.get(f"{p}/api/v1/screens/{screen}")).json()["device"]
        assert dev["presence"] == "revoked" and dev["revoked_at"]


async def test_content_entry_script_is_served(admin: AsyncClient, app: FastAPI, p: str) -> None:
    """Le script annoncé au player (programmation) doit exister sous le base path."""
    entry = app.state.runtime.registry.content_entry("content.image")
    assert entry["script"] == "modules/content.image/assets/player.js"
    r = await admin.get(f"{p}/{entry['script']}")
    assert r.status_code == 200, r.text
