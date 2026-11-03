"""GET /api/v1/player/feeds/{id} et `feeds` dans la programmation, aux deux VDS_BASE_PATH."""

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import _diffusion_clean, mk_playlist, mk_screen  # noqa: F401
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401
from vds_sdk.server.http import SafeHttpError, SafeResponse

FIX = Path(__file__).parent / "fixtures"
URL = "https://news.example.org/feed.xml"


class FakeNet:
    """Remplace `SafeHttp` du noyau : aucune sortie réseau pendant les tests."""

    def __init__(self) -> None:
        self.body = (FIX / "rss2.xml").read_bytes()
        self.fail: str | None = None
        self.urls: list[str] = []

    async def get(self, url: str, **kw: Any) -> SafeResponse:
        self.urls.append(url)
        if self.fail:
            raise SafeHttpError(self.fail)
        return SafeResponse(status=200, headers={}, body=self.body, final_url=url)


@pytest.fixture
async def net(app: FastAPI, migrated_db_url: str) -> FakeNet:
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE content_feed"))
    await engine.dispose()
    fake = FakeNet()
    app.state.runtime.safe_http = fake
    return fake


async def add_rss(admin: AsyncClient, p: str, playlist: str, **props: Any) -> None:
    r = await admin.post(
        f"{p}/api/v1/playlists/{playlist}/items",
        json={"content_type": "content.rss", "props": {"url": URL, **props}},
        headers=SAME,
    )
    assert r.status_code == 201, r.text


async def schedule(admin: AsyncClient, p: str, playlist: str, screen: str) -> None:
    body = {"name": "S", "playlist_id": playlist, "target_type": "screen", "target_id": screen}
    r = await admin.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 201, r.text


async def feed_ids(player: AsyncClient, p: str, token: str) -> list[str]:
    r = await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))
    assert r.status_code == 200, r.text
    return [
        f
        for pl in r.json()["playlists"].values()
        for it in pl["items"]
        for f in it.get("feeds", [])
    ]


async def test_feed_flow(admin: AsyncClient, app: FastAPI, p: str, net: FakeNet) -> None:
    site, screen = await mk_screen(admin, p)
    r = await admin.post(
        f"{p}/api/v1/screens", json={"site_id": site, "name": "Cantine"}, headers=SAME
    )
    other_screen = str(r.json()["id"])
    pl = await mk_playlist(admin, p)
    await add_rss(admin, p, pl, max_items=3, item_duration_s=5)
    await schedule(admin, p, pl, screen)
    runtime = app.state.runtime
    async with new_client(app) as player, new_client(app) as intruder:
        token = await pair(admin, player, p, screen)
        other_token = await pair(admin, intruder, p, other_screen)
        doc = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        item = next(iter(doc["playlists"].values()))["items"][0]
        (fid,) = item["feeds"]
        assert item["props"]["feed_id"] == fid and item["duration_ms"] == 15000
        url = f"{p}/api/v1/player/feeds/{fid}"

        assert (await player.get(url)).status_code == 401  # sans jeton
        assert (await player.get(url, headers=hdr("vds_dev_x"))).status_code == 401
        # existe mais jamais récupéré : 204
        assert (await player.get(url, headers=hdr(token))).status_code == 204
        # le rafraîchissement du noyau (aucune sortie réseau réelle)
        assert await runtime.feeds.run_once() == 1 and net.urls == [URL]
        r = await player.get(url, headers=hdr(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["feed_id"] == fid and body["fetched_at"].endswith(("Z", "+00:00"))
        assert body["data"]["title"] == "Le Journal & Co" and len(body["data"]["items"]) == 3
        assert r.headers["etag"] and "private" in r.headers["cache-control"]
        etag = r.headers["etag"]
        nm = await player.get(url, headers={**hdr(token), "If-None-Match": etag})
        assert nm.status_code == 304 and nm.content == b""
        # un autre écran n'a pas ce flux dans sa programmation : 404, sans fuite
        denied = await intruder.get(url, headers=hdr(other_token))
        assert denied.status_code == 404 and denied.json()["code"] == "feeds.unknown"
        # inconnu / mal formé : même réponse
        for bad in ("0" * 32, "G" * 32, "x"):
            gone = await player.get(f"{p}/api/v1/player/feeds/{bad}", headers=hdr(token))
            assert gone.status_code == 404 and gone.json()["code"] == "feeds.unknown", bad
        # nouvel état => nouvel ETag
        net.body = (FIX / "atom.xml").read_bytes()
        async with runtime.maker.begin() as s:
            await s.execute(text("UPDATE content_feed SET next_at = now() - interval '1 s'"))
        await runtime.feeds.run_once()
        again = await player.get(url, headers={**hdr(token), "If-None-Match": etag})
        assert again.status_code == 200 and again.json()["data"]["title"] == "Flux Atom"
        # échec : le dernier état connu reste servi
        net.fail = "net.timeout"
        async with runtime.maker.begin() as s:
            await s.execute(text("UPDATE content_feed SET next_at = now() - interval '1 s'"))
        await runtime.feeds.run_once()
        still = await player.get(url, headers=hdr(token))
        assert still.status_code == 200 and still.json()["data"]["title"] == "Flux Atom"
        # la route est montée sous VDS_BASE_PATH seulement
        if p:
            assert (
                await player.get(f"/api/v1/player/feeds/{fid}", headers=hdr(token))
            ).status_code == 404


async def test_item_invalid_url_not_in_schedule(
    admin: AsyncClient, app: FastAPI, p: str, net: FakeNet
) -> None:
    _, screen = await mk_screen(admin, p)
    pl = await mk_playlist(admin, p)
    await add_rss(admin, p, pl)
    await add_rss(admin, p, pl, max_items=2)  # même URL : un seul flux
    await schedule(admin, p, pl, screen)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        assert len(set(await feed_ids(player, p, token))) == 1
        assert len(await feed_ids(player, p, token)) == 2


async def test_feed_read_does_not_rebuild_schedule(
    admin: AsyncClient, app: FastAPI, p: str, net: FakeNet, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vds_server.player.schedule import ScheduleBuilder

    _, screen = await mk_screen(admin, p)
    pl = await mk_playlist(admin, p)
    await add_rss(admin, p, pl)
    await schedule(admin, p, pl, screen)
    runtime = app.state.runtime
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        (fid,) = await feed_ids(player, p, token)  # la synchro alimente le cache
        builds = 0
        real = ScheduleBuilder.build

        async def counted(self: ScheduleBuilder, *a: Any, **k: Any) -> dict[str, Any]:
            nonlocal builds
            builds += 1
            return await real(self, *a, **k)

        monkeypatch.setattr(ScheduleBuilder, "build", counted)
        url = f"{p}/api/v1/player/feeds/{fid}"
        for _ in range(3):
            assert (await player.get(url, headers=hdr(token))).status_code == 204
        assert builds == 0
        # invalidation (publication) : une seule reconstruction, puis cache de nouveau
        runtime.feed_access.invalidate()
        for _ in range(3):
            assert (await player.get(url, headers=hdr(token))).status_code == 204
        assert builds == 1
