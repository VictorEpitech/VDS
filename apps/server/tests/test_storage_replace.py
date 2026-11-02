"""Remplacement de fichier d'un média (révision N+1), deux VDS_BASE_PATH."""

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401, F811
from test_diffusion_media_files import (  # noqa: F401, F811 - fixtures partagées
    BAD_PNG,
    PDF,
    PNG,
    _clean_media,
    content_modules,
    media_dir,
    stored_files,
    upload,
)
from test_pairing import _pairing_clean  # noqa: F401


@pytest.fixture(autouse=True)
async def _clean_tombstones(migrated_db_url: str) -> None:
    await sql(migrated_db_url, "TRUNCATE storage_tombstone")


def _png(color: str) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, "PNG")
    return buf.getvalue()


async def sql(url: str, query: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        res = await c.execute(text(query), params)
        rows = [tuple(r) for r in res.all()] if res.returns_rows else []
    await engine.dispose()
    return rows


async def replace(
    c: AsyncClient,
    p: str,
    media_id: object,
    data: bytes,
    *,
    mime: str = "image/png",
    if_match: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = dict(SAME) | ({"If-Match": if_match} if if_match else {})
    r = await c.post(
        f"{p}/api/v1/media/{media_id}/file", files={"file": ("b.png", data, mime)}, headers=headers
    )
    return r.status_code, r.json()


async def test_replace_bumps_revision_and_tombstones_old(
    content_modules: None, admin: AsyncClient, p: str, migrated_db_url: str, media_dir: Path
) -> None:
    _, first = await upload(admin, p, _png("red"))
    mid = first["id"]
    old_keys = {
        r[0]
        for r in await sql(
            migrated_db_url, "SELECT storage_key FROM media_variant WHERE media_id=:mid", mid=mid
        )
    }
    new = _png("blue")
    status, body = await replace(admin, p, mid, new)
    assert status == 200, body
    assert body["id"] == mid and body["sha256"] != first["sha256"]
    rows = await sql(
        migrated_db_url,
        "SELECT name, storage_key FROM media_variant WHERE media_id=:mid ORDER BY name",
        mid=mid,
    )
    assert [r[0] for r in rows] == ["original", "thumb"]
    assert all("-r2." in str(r[1]) for r in rows)
    assert (await sql(migrated_db_url, "SELECT revision FROM media WHERE id=:mid", mid=mid)) == [
        (2,)
    ]
    tomb = await sql(
        migrated_db_url, "SELECT key, reason, not_before FROM storage_tombstone ORDER BY key"
    )
    assert {t[0] for t in tomb} == old_keys and {t[1] for t in tomb} == {"replaced"}
    delta = tomb[0][2] - datetime.now(UTC)  # type: ignore[operator]
    assert timedelta(hours=47) < delta < timedelta(hours=49)
    served = await admin.get(f"{p}/media/{mid}/original")
    assert served.content == new
    names = {f.name for f in stored_files(media_dir)}
    assert any("-r2" in n for n in names) and any(n.startswith("original.") for n in names)
    third = await replace(admin, p, mid, _png("green"))
    assert third[0] == 200
    assert (await sql(migrated_db_url, "SELECT revision FROM media WHERE id=:mid", mid=mid)) == [
        (3,)
    ]
    assert (
        await sql(
            migrated_db_url,
            "SELECT count(*) FROM audit_log WHERE action='media.replaced' AND target_id=:mid",
            mid=mid,
        )
    ) == [(2,)]


async def test_replace_same_sha_is_noop(
    content_modules: None, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    data = _png("red")
    _, first = await upload(admin, p, data)
    status, body = await replace(admin, p, first["id"], data)
    assert status == 200 and body["sha256"] == first["sha256"]
    assert (await sql(migrated_db_url, "SELECT revision FROM media")) == [(1,)]
    assert (await sql(migrated_db_url, "SELECT count(*) FROM storage_tombstone")) == [(0,)]


async def test_replace_kind_mismatch_422(
    content_modules: None, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    _, first = await upload(admin, p, _png("red"))
    status, body = await replace(admin, p, first["id"], PDF, mime="application/pdf")
    assert status == 422 and body["code"] == "media.replace_kind_mismatch"
    assert (await sql(migrated_db_url, "SELECT revision FROM media")) == [(1,)]


async def test_replace_processing_failure_keeps_revision(
    content_modules: None, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    _, first = await upload(admin, p, _png("red"))
    status, body = await replace(admin, p, first["id"], BAD_PNG)
    assert status == 422 and body["code"] == "media.processing_failed"
    assert (await sql(migrated_db_url, "SELECT revision, sha256 FROM media")) == [
        (1, first["sha256"])
    ]
    tomb = await sql(migrated_db_url, "SELECT reason, key FROM storage_tombstone")
    assert tomb and {t[0] for t in tomb} == {"aborted"} and all("-r2" in str(t[1]) for t in tomb)
    assert (await admin.get(f"{p}/media/{first['id']}/original")).status_code == 200


async def test_replace_if_match(
    content_modules: None, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    _, first = await upload(admin, p, _png("red"))
    status, body = await replace(admin, p, first["id"], _png("blue"), if_match='"' + "0" * 64 + '"')
    assert status == 412 and body["code"] == "media.precondition_failed"
    status, _ = await replace(admin, p, first["id"], _png("blue"), if_match=f'"{first["sha256"]}"')
    assert status == 200


async def test_replace_changes_player_schedule_revision(
    content_modules: None, admin: AsyncClient, app: FastAPI, p: str
) -> None:
    from test_diffusion import mk_playlist, mk_screen
    from test_pairing import hdr, pair
    from test_player_schedule import mk_schedule

    _, screen = await mk_screen(admin, p)
    _, media = await upload(admin, p, _png("red"))
    pl = await mk_playlist(admin, p)
    r = await admin.post(
        f"{p}/api/v1/playlists/{pl}/items",
        json={"content_type": "content.image", "props": {"media": media["id"]}},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    await mk_schedule(admin, p, pl, screen)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        before = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        assert (await replace(admin, p, media["id"], _png("blue")))[0] == 200
        after = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
    assert before["revision"] != after["revision"]
    key = f"media/{media['id']}/original"
    assert before["assets"][key]["sha256"] != after["assets"][key]["sha256"]


async def test_replace_requires_same_origin_and_editor(
    content_modules: None, admin: AsyncClient, app: FastAPI, p: str
) -> None:
    _, first = await upload(admin, p, _png("red"))
    r = await admin.post(
        f"{p}/api/v1/media/{first['id']}/file", files={"file": ("b.png", _png("blue"), "image/png")}
    )
    assert r.status_code == 403
    async with new_client(app) as nobody:
        r = await nobody.post(
            f"{p}/api/v1/media/{first['id']}/file",
            files={"file": ("b.png", _png("blue"), "image/png")},
            headers=SAME,
        )
    assert r.status_code in (401, 403)
    r = await admin.post(
        f"{p}/api/v1/media/{'0' * 8}-0000-0000-0000-{'0' * 12}/file",
        files={"file": ("b.png", _png("blue"), "image/png")},
        headers=SAME,
    )
    assert r.status_code == 404
