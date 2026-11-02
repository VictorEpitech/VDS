"""Expiration à la résolution : `until` par élément, playlist qui expire, 404 appareil, forçages."""

import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401, F811
from test_diffusion import (  # noqa: F401, F811 - fixtures partagées
    _content_types,
    _diffusion_clean,
    mk_screen,
)
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401, F811
from test_player_schedule import (  # noqa: F401, F811
    _entries,
    schedule_media_sql,
)
from vds_server.db.models_override import PlaybackOverride
from vds_server.player.schedule import override_spec


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


PNG = _png()


async def exec_sql(url: str, query: str, **params: Any) -> None:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text(query), params)
    await engine.dispose()


async def scheduled_media(admin: AsyncClient, p: str, url: str, screen: str) -> UUID:
    up = await admin.post(
        f"{p}/api/v1/media", files={"file": ("a.png", PNG, "image/png")}, headers=SAME
    )
    assert up.status_code == 201, up.text
    media = UUID(up.json()["id"])
    await schedule_media_sql(url, screen, media)
    return media


async def items_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for pl in doc["playlists"].values() for i in pl["items"]]


async def test_media_until_then_omitted_and_404(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    media = await scheduled_media(admin, p, migrated_db_url, screen)
    soon = datetime.now(UTC) + timedelta(hours=2)
    await exec_sql(
        migrated_db_url, "UPDATE media SET expires_at = :e WHERE id = :m", e=soon, m=media
    )
    url = f"{p}/api/v1/player/schedule"
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        first = (await player.get(url, headers=hdr(token))).json()
        item = (await items_of(first))[0]
        assert datetime.fromisoformat(item["until"]) == soon.replace(microsecond=soon.microsecond)
        second = (await player.get(url, headers=hdr(token))).json()
        assert second["revision"] == first["revision"]  # stable avant l'instant
        assert (
            await player.get(f"{p}/media/{media}/original", headers=hdr(token))
        ).status_code == 200
        await exec_sql(
            migrated_db_url,
            "UPDATE media SET expires_at = now() - interval '1 minute' WHERE id = :m",
            m=media,
        )
        after = (await player.get(url, headers=hdr(token))).json()
        assert await items_of(after) == [] and after["revision"] != first["revision"]
        gone = await player.get(f"{p}/media/{media}/original", headers=hdr(token))
        assert gone.status_code == 404 and gone.json()["code"] == "media.not_found"
        # la session admin lit encore (aperçu pendant la rétention)
        assert (await admin.get(f"{p}/media/{media}/original")).status_code == 200


async def test_expired_at_flag_alone_stops_diffusion(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    media = await scheduled_media(admin, p, migrated_db_url, screen)
    await exec_sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() + interval '1 day', expired_at = now() WHERE id = :m",
        m=media,
    )
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        doc = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        assert await items_of(doc) == []


async def test_playlist_expiry_cuts_schedule(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    await scheduled_media(admin, p, migrated_db_url, screen)
    cut = datetime.now(UTC) + timedelta(hours=3)
    await exec_sql(migrated_db_url, "UPDATE playlist SET expires_at = :e", e=cut)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        doc = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        kinds = [s["kind"] for s in doc["slots"]]
        assert kinds == ["play", "off"]
        assert datetime.fromisoformat(doc["slots"][0]["end"]) == cut
        assert datetime.fromisoformat(doc["slots"][1]["start"]) == cut
        # déjà expirée : l'après (donc le présent) est hors service
        await exec_sql(
            migrated_db_url, "UPDATE playlist SET expires_at = now() - interval '1 minute'"
        )
        doc = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        assert doc["slots"][-1]["kind"] == "off"
        assert datetime.fromisoformat(doc["slots"][-1]["start"]) < datetime.now(UTC)


def test_override_ends_at_playlist_expiry() -> None:
    now = datetime.now(UTC)
    row = PlaybackOverride(
        id=uuid4(),
        target_type="screen",
        target_id=uuid4(),
        starts_at=now,
        ends_at=now + timedelta(hours=5),
        playlist_id=uuid4(),
        priority=0,
        created_at=now,
    )
    cut = now + timedelta(hours=1)
    spec = override_spec(row, False, cut)
    assert spec is not None and spec.ends_at == cut
    ended = override_spec(row, False, now - timedelta(minutes=1))
    assert ended is None  # playlist déjà expirée : le forçage n'a jamais joué
    plain = override_spec(row, False, None)
    assert plain is not None and plain.ends_at == row.ends_at
