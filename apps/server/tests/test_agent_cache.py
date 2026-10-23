"""Cache média de site : manifeste agent, lecture des médias par jeton d'agent, clé HMAC."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import _queue_clean, auth, connect, setup  # noqa: F401
from test_auth import admin, anon, app, base, clean, new_client, p  # noqa: F401
from test_diffusion_media_files import PNG, upload
from test_player_schedule import schedule_media_sql
from vds_server.agents.cache import cache_info, cache_key
from vds_server.db.models_power import SiteAgent


async def test_manifest_and_media_read(
    app: FastAPI,
    admin: AsyncClient,
    p: str,
    setup: dict[str, Any],
    migrated_db_url: str,  # noqa: F811
) -> None:
    _, mine = await upload(admin, p)
    _, other = await upload(admin, p)
    await schedule_media_sql(migrated_db_url, setup["screen"], UUID(str(mine["id"])))
    h = auth(setup["token"])
    async with new_client(app) as agent:
        r = await agent.get(f"{p}/api/v1/agent/cache/manifest", headers=h)
        assert r.status_code == 200
        items = r.json()["items"]
        assert [i["media_id"] for i in items] == [mine["id"]] * len(items) and items
        orig = next(i for i in items if i["variant"] == "original")
        assert orig["sha256"] and orig["bytes"] == len(PNG)
        etag = r.headers["ETag"]
        r = await agent.get(f"{p}/api/v1/agent/cache/manifest", headers=h | {"If-None-Match": etag})
        assert r.status_code == 304
        # lecture : médias du site uniquement
        ok = await agent.get(f"{p}/media/{mine['id']}/original", headers=h)
        assert (ok.status_code, ok.content) == (200, PNG)
        no = await agent.get(f"{p}/media/{other['id']}/original", headers=h)
        assert (no.status_code, no.json()["code"]) == (403, "media.forbidden")
        # sans jeton / jeton inconnu
        assert (await agent.get(f"{p}/api/v1/agent/cache/manifest")).status_code == 401
        bad = auth("vds_agt_inconnu")
        assert (await agent.get(f"{p}/api/v1/agent/cache/manifest", headers=bad)).status_code == 401
        assert (await agent.get(f"{p}/media/{mine['id']}/original", headers=bad)).status_code == 401


async def test_welcome_cache_key(
    app: FastAPI,
    p: str,
    setup: dict[str, Any],  # noqa: F811
) -> None:
    assert (
        cache_info(None, SiteAgent(id=UUID(str(setup["agent"])), rotated_at=None)).enabled is False
    )
    app.state.runtime.settings.secret_key = "s3cret"  # noqa: S105
    ws = await connect(app, p, setup["token"])
    cache = (await ws.hello())["cache"]
    assert cache["enabled"] and len(cache["key"]) == 64
    kid, key = cache_key("s3cret", UUID(setup["agent"]), None)
    assert (cache["key_id"], cache["key"]) == (kid, key)
    assert (
        cache_key("s3cret", UUID(setup["agent"]), None)[1]
        != cache_key("autre", UUID(setup["agent"]), None)[1]
    )
