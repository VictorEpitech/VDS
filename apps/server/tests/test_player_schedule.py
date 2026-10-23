"""GET /api/v1/player/schedule : auth, ETag/304, révision, médias, aux deux VDS_BASE_PATH."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401
from test_diffusion import (  # noqa: F401 - fixtures partagées
    _content_types,
    _diffusion_clean,
    make_media,
    mk_playlist,
    mk_screen,
)
from test_pairing import _pairing_clean, hdr, pair, sql  # noqa: F401
from vds_server.player.schedule import ScheduleBuilder

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8


async def schedule_media_sql(url: str, screen_id: str, media_id: UUID) -> None:
    """Programme (SQL direct) un média sur un écran : playlist + élément + schedule."""
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        pl = (
            await c.execute(text("INSERT INTO playlist (name) VALUES ('t') RETURNING id"))
        ).scalar_one()
        item = (
            await c.execute(
                text(
                    "INSERT INTO playlist_item (playlist_id, position, content_type, props) "
                    "VALUES (:pl, 0, 'content.image', CAST(:props AS jsonb)) RETURNING id"
                ),
                {"pl": pl, "props": json.dumps({"src": str(media_id)})},
            )
        ).scalar_one()
        await c.execute(
            text("INSERT INTO playlist_item_media (item_id, media_id) VALUES (:i, :m)"),
            {"i": item, "m": media_id},
        )
        await c.execute(
            text(
                "INSERT INTO schedule (name, playlist_id, target_type, target_id) "
                "VALUES ('t', :pl, 'screen', :s)"
            ),
            {"pl": pl, "s": UUID(screen_id)},
        )
    await engine.dispose()


@pytest.fixture(autouse=True)
def _entries(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {
        "version": "1.0.0",
        "element": "vds-content-image",
        "script": "modules/content.image/assets/player.js",
        "requires_network": False,
    }
    monkeypatch.setattr(
        app.state.runtime.registry,
        "content_entry",
        lambda m: entry if m == "content.image" else None,
    )
    # schémas factices (`src`) : pas de résolution par les vrais modules actifs
    registry = app.state.runtime.registry
    real = registry.instance
    monkeypatch.setattr(
        registry, "instance", lambda m: None if m.startswith("content.") else real(m)
    )


async def mk_schedule(c: AsyncClient, p: str, playlist: str, target: str, **kw: Any) -> str:
    body = {
        "name": "S",
        "playlist_id": playlist,
        "target_type": "screen",
        "target_id": target,
        **kw,
    }
    r = await c.post(f"{p}/api/v1/schedules", json=body, headers=SAME)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def mk_item(c: AsyncClient, p: str, playlist: str, media: UUID) -> None:
    r = await c.post(
        f"{p}/api/v1/playlists/{playlist}/items",
        json={"content_type": "content.image", "props": {"src": str(media)}, "duration_ms": 5000},
        headers=SAME,
    )
    assert r.status_code == 201, r.text


async def test_schedule_flow(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    media = await make_media(migrated_db_url)
    pl = await mk_playlist(admin, p)
    await mk_item(admin, p, pl, media)
    url = f"{p}/api/v1/player/schedule"
    async with new_client(app) as player:
        assert (await player.get(url)).status_code == 401
        assert (await player.get(url, headers=SAME)).status_code == 401
        token = await pair(admin, player, p, screen)
        assert (await player.get(url, headers=hdr("vds_dev_x"))).status_code == 401

        r = await player.get(f"{url}?format=1", headers=hdr(token))
        assert r.status_code == 200, r.text
        doc = r.json()
        assert doc["format"] == 1 and doc["timezone"] == "Europe/Paris"
        assert [s["kind"] for s in doc["slots"]] == ["off"]
        assert doc["slots"][0]["start"] == doc["valid_from"]
        assert doc["slots"][0]["end"] == doc["valid_until"]
        assert doc["screen"]["id"] == screen and doc["playlists"] == {}
        etag = r.headers["etag"]
        assert etag == f'"{doc["revision"]}"' and doc["revision"].startswith("sha256:")

        bad = await player.get(f"{url}?format=2", headers=hdr(token))
        assert bad.status_code == 400
        assert bad.json()["code"] == "errors.schedule_format_unsupported"

        # révision stable, 304 sur If-None-Match
        again = await player.get(url, headers=hdr(token))
        assert again.json()["revision"] == doc["revision"]
        h = {**hdr(token), "If-None-Match": etag}
        nm = await player.get(url, headers=h)
        assert nm.status_code == 304 and nm.content == b"" and nm.headers["etag"] == etag
        h2 = {**hdr(token), "If-None-Match": f'"x", W/{etag}'}
        assert (await player.get(url, headers=h2)).status_code == 304

        # le heartbeat annonce la vraie révision
        hb = await player.post(
            f"{p}/api/v1/player/heartbeat", json={"revision": "sha256:old"}, headers=hdr(token)
        )
        assert hb.json()["revision"] == doc["revision"]

        # une programmation change la révision, le 304 cesse
        await mk_schedule(admin, p, pl, screen)
        r2 = await player.get(url, headers=h)
        assert r2.status_code == 200
        new = r2.json()
        assert new["revision"] != doc["revision"]
        assert [s["kind"] for s in new["slots"]] == ["play"]
        zone = new["slots"][0]["layout"]["zones"][0]
        assert zone["id"] == "main" and (zone["w"], zone["h"]) == (1, 1)
        items = new["playlists"][zone["playlist"]]["items"]
        key = f"media/{media}/original"
        assert items[0]["props"] == {"src": key} and items[0]["assets"] == [key]
        assert items[0]["duration_ms"] == 5000 and items[0]["requires_network"] is False
        assert new["assets"][key]["mime"] == "image/png" and new["assets"][key]["bytes"] == 10
        assert new["content_types"]["content.image"]["element"] == "vds-content-image"
        # URLs relatives : jamais d'hôte ni de chemin absolu
        flat = json.dumps(new)
        assert "http://" not in flat and "https://" not in flat and '"/' not in flat
        hb = await player.post(f"{p}/api/v1/player/heartbeat", json={}, headers=hdr(token))
        assert hb.json()["revision"] == new["revision"]


async def test_device_media_scoped_to_schedule(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    ids: list[UUID] = []
    for name in ("mine.png", "other.png"):
        up = await admin.post(
            f"{p}/api/v1/media", files={"file": (name, PNG, "image/png")}, headers=SAME
        )
        assert up.status_code == 201, up.text
        ids.append(UUID(up.json()["id"]))
    mine, other = ids
    await schedule_media_sql(migrated_db_url, screen, mine)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        ok = await player.get(f"{p}/media/{mine}/original", headers=hdr(token))
        assert ok.status_code == 200 and ok.content == PNG
        r = await player.get(f"{p}/media/{other}/original", headers=hdr(token))
        assert r.status_code == 403 and r.json()["code"] == "media.forbidden"
        nope = await player.get(f"{p}/media/nope/original", headers=hdr(token))
        assert nope.status_code == 404
        # la session d'un utilisateur lit toujours tout
        assert (await admin.get(f"{p}/media/{other}/original")).status_code == 200


async def _tz(_screen_id: UUID) -> str:
    return "Europe/Paris"


async def test_dst_through_builder(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    media = await make_media(migrated_db_url)
    pl = await mk_playlist(admin, p)
    await mk_item(admin, p, pl, media)
    rules = [{"weekdays": 127, "start": "08:00", "end": "19:00"}]
    await mk_schedule(admin, p, pl, screen, rules=rules)
    rt = app.state.runtime
    async with rt.maker() as session:
        b = ScheduleBuilder(
            session, rt.registry.content_item_schema, rt.registry.content_entry, _tz
        )
        doc = await b.build(UUID(screen), datetime(2026, 10, 24, 10, 0, tzinfo=UTC))
        doc2 = await b.build(UUID(screen), datetime(2026, 10, 24, 11, 0, tzinfo=UTC))
    assert doc["valid_from"] == "2026-10-24T00:00:00+02:00"
    assert doc["valid_until"] == "2026-10-26T00:00:00+01:00"
    starts = [s["start"] for s in doc["slots"] if s["kind"] == "play"]
    assert starts == ["2026-10-24T08:00:00+02:00", "2026-10-25T08:00:00+01:00"]
    slots = doc["slots"]
    assert [a["end"] for a in slots[:-1]] == [s["start"] for s in slots[1:]]  # sans trou
    assert doc["revision"] == doc2["revision"] and doc["server_time"] != doc2["server_time"]


async def test_other_screen_schedule_not_served(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    site, screen = await mk_screen(admin, p)
    r = await admin.post(f"{p}/api/v1/screens", json={"site_id": site, "name": "B"}, headers=SAME)
    other = str(r.json()["id"])
    media = await make_media(migrated_db_url)
    pl = await mk_playlist(admin, p)
    await mk_item(admin, p, pl, media)
    await mk_schedule(admin, p, pl, other)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        doc = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        assert [s["kind"] for s in doc["slots"]] == ["off"] and doc["assets"] == {}
