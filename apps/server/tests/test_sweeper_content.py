"""Balayeurs de la médiathèque (expire, purge, gc), suppression manuelle, media_reprocess."""

import asyncio
import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import SAME, admin, anon, app, base, clean, editor, new_client, p  # noqa: F401, F811
from test_diffusion import mk_playlist, mk_screen  # noqa: F401, F811
from test_diffusion_media_files import (  # noqa: F401, F811
    _clean_media,
    content_modules,
    media_dir,
    stored_files,
    upload,
)
from test_pairing import _pairing_clean  # noqa: F401, F811
from vds_server.media_reprocess import reprocess


def _png(color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color).save(buf, "PNG")
    return buf.getvalue()


async def sql(url: str, query: str, **params: Any) -> list[tuple[Any, ...]]:
    engine = create_async_engine(url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        res = await c.execute(text(query), params)
        rows = [tuple(r) for r in res.all()] if res.returns_rows else []
    await engine.dispose()
    return rows


@pytest.fixture(autouse=True)
async def _clean_content(migrated_db_url: str) -> AsyncIterator[None]:
    await sql(migrated_db_url, "TRUNCATE storage_tombstone, playlist CASCADE")
    yield


async def mk_item(c: AsyncClient, p: str, playlist: str, media: str) -> None:
    r = await c.post(
        f"{p}/api/v1/playlists/{playlist}/items",
        json={"content_type": "content.image", "props": {"media": media}, "duration_ms": 5000},
        headers=SAME,
    )
    assert r.status_code == 201, r.text


def rt(app: FastAPI) -> Any:
    return app.state.runtime


async def test_expire_is_idempotent_across_workers(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    media = (await upload(admin, p, _png()))[1]["id"]
    pl = await mk_playlist(admin, p)
    await sql(migrated_db_url, "UPDATE media SET expires_at = now() - interval '1 minute'")
    await sql(migrated_db_url, "UPDATE playlist SET expires_at = now() - interval '1 minute'")
    sweeper = rt(app).content_sweeper
    results = await asyncio.gather(sweeper.expire(), sweeper.expire())
    names = sorted(e.name for batch in results for e in batch)
    assert names == ["media.expired", "playlist.expired"]
    assert await sweeper.expire() == []
    rows = await sql(
        migrated_db_url, "SELECT action FROM audit_log WHERE action LIKE '%.expired' ORDER BY 1"
    )
    assert [r[0] for r in rows if r[0] in ("media.expired", "playlist.expired")][-2:] == [
        "media.expired",
        "playlist.expired",
    ]
    assert (await sql(migrated_db_url, "SELECT count(*) FROM media WHERE expired_at IS NOT NULL"))[
        0
    ] == (1,)
    assert media and pl


async def test_restore_during_retention_and_not_purged(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    media = (await upload(admin, p, _png()))[1]["id"]
    await sql(migrated_db_url, "UPDATE media SET expires_at = now() - interval '1 minute'")
    await rt(app).content_sweeper.expire()
    # dans la rétention : rien n'est purgé
    assert await rt(app).content_sweeper.purge() == []
    assert (await admin.get(f"{p}/api/v1/media/{media}")).status_code == 200


async def test_purge_media_removes_items_keeps_audit_and_deletes_files(
    content_modules: None,
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    migrated_db_url: str,
    media_dir: Path,
) -> None:
    keep = (await upload(admin, p, _png("blue")))[1]["id"]
    dead = (await upload(admin, p, _png("red")))[1]["id"]
    pl = await mk_playlist(admin, p)
    await mk_item(admin, p, pl, str(keep))
    await mk_item(admin, p, pl, str(dead))
    await sql(
        migrated_db_url,
        "UPDATE media SET expires_at = now() - interval '40 days', "
        "expired_at = now() - interval '31 days' WHERE id = :m",
        m=UUID(str(dead)),
    )
    sweeper = rt(app).content_sweeper
    events = await sweeper.purge()
    names = [e.name for e in events]
    assert "media.purged" in names and "playlist.updated" in names
    purged = next(e for e in events if e.name == "media.purged")
    assert purged.payload["playlist_ids"] == [pl]
    assert (await sql(migrated_db_url, "SELECT count(*) FROM media"))[0] == (1,)
    assert (
        await sql(
            migrated_db_url, "SELECT count(*) FROM playlist_item WHERE playlist_id = :p", p=UUID(pl)
        )
    )[0] == (1,)
    tomb = await sql(migrated_db_url, "SELECT reason, key FROM storage_tombstone")
    assert tomb and {t[0] for t in tomb} == {"purged"}
    audit = await sql(
        migrated_db_url,
        "SELECT details FROM audit_log WHERE action = 'media.purged' AND target_id = :m",
        m=str(dead),
    )
    assert audit and audit[0][0]["removed_items"][0]["playlist_id"] == pl
    # idempotent
    assert await sweeper.purge() == []
    # gc supprime les fichiers
    before = len(stored_files(media_dir))
    assert await sweeper.gc() == len(tomb)
    assert len(stored_files(media_dir)) == before - len(tomb)
    assert (await sql(migrated_db_url, "SELECT count(*) FROM storage_tombstone"))[0] == (0,)


async def test_purge_playlist_removes_schedules_and_waits_for_override(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    _, screen = await mk_screen(admin, p)
    pl = await mk_playlist(admin, p)
    r = await admin.post(
        f"{p}/api/v1/schedules",
        json={"name": "S", "playlist_id": pl, "target_type": "screen", "target_id": screen},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    schedule_id = r.json()["id"]
    await sql(
        migrated_db_url,
        "UPDATE playlist SET expires_at = now() - interval '40 days', "
        "expired_at = now() - interval '35 days' WHERE id = :p",
        p=UUID(pl),
    )
    await sql(
        migrated_db_url,
        "INSERT INTO playback_overrides (name, kind, playlist_id, target_type, target_id, "
        "starts_at, ends_at, created_at, updated_at) VALUES ('o', 'playlist', :p, 'screen', :s, "
        "now() - interval '36 days', now() - interval '35 days', now(), now())",
        p=UUID(pl),
        s=UUID(screen),
    )
    sweeper = rt(app).content_sweeper
    assert await sweeper.purge() == []  # reportée : un forçage référence la playlist
    assert (await sql(migrated_db_url, "SELECT count(*) FROM playlist"))[0] == (1,)
    await sql(migrated_db_url, "DELETE FROM playback_overrides")
    events = await sweeper.purge()
    assert sorted(e.name for e in events) == ["playlist.purged", "schedule.deleted"]
    deleted = next(e for e in events if e.name == "schedule.deleted")
    assert deleted.payload == {"schedule_id": schedule_id, "reason": "playlist_purged"}
    assert (await sql(migrated_db_url, "SELECT count(*) FROM schedule"))[0] == (0,)
    assert (await sql(migrated_db_url, "SELECT count(*) FROM playlist"))[0] == (0,)
    assert (
        await sql(
            migrated_db_url, "SELECT count(*) FROM audit_log WHERE action = 'playlist.purged'"
        )
    )[0][0] >= 1


async def test_gc_failure_keeps_row_then_retries_and_invalid_key_rejected(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str
) -> None:
    await admin.get(f"{p}/api/v1/media")  # démarre le runtime
    await sql(
        migrated_db_url,
        "INSERT INTO storage_tombstone (backend, key, reason, not_before) VALUES "
        "('storage.local', '../../etc/passwd', 'deleted', now() - interval '1 minute'), "
        "('storage.absent', 'aa/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/original.png', 'deleted', "
        "now() - interval '1 minute')",
    )
    sweeper = rt(app).content_sweeper
    assert await sweeper.gc() == 0
    rows = await sql(
        migrated_db_url, "SELECT backend, attempts, last_error FROM storage_tombstone ORDER BY 1"
    )
    assert rows[0][0] == "storage.absent" and rows[0][1] == 0  # backend absent : attend
    assert rows[1][1] == 1 and "key_invalid" in rows[1][2]  # clé rejetée par storage.local
    future = await sql(
        migrated_db_url,
        "SELECT not_before > now() FROM storage_tombstone WHERE backend = 'storage.local'",
    )
    assert future == [(True,)]  # backoff
    # au-delà de 10 échecs : audit et abandon des tentatives
    await sql(
        migrated_db_url,
        "UPDATE storage_tombstone SET attempts = 9, not_before = now() - interval '1 minute' "
        "WHERE backend = 'storage.local'",
    )
    await sweeper.gc()
    assert (
        await sql(
            migrated_db_url, "SELECT count(*) FROM audit_log WHERE action = 'storage.delete_failed'"
        )
    )[0][0] >= 1
    await sql(
        migrated_db_url,
        "UPDATE storage_tombstone SET not_before = now() - interval '1 minute'",
    )
    await sweeper.gc()
    assert (
        await sql(
            migrated_db_url,
            "SELECT attempts FROM storage_tombstone WHERE backend = 'storage.local'",
        )
    )[0] == (10,)


async def test_manual_delete_goes_through_tombstone(
    admin: AsyncClient, app: FastAPI, p: str, migrated_db_url: str, media_dir: Path
) -> None:
    media = (await upload(admin, p, _png()))[1]["id"]
    assert len(stored_files(media_dir)) >= 1
    r = await admin.delete(f"{p}/api/v1/media/{media}", headers=SAME)
    assert r.status_code == 204
    tomb = await sql(migrated_db_url, "SELECT reason FROM storage_tombstone")
    assert tomb and {t[0] for t in tomb} == {"deleted"}
    assert len(stored_files(media_dir)) >= 1  # rien n'est supprimé avant le balayeur
    await rt(app).content_sweeper.gc()
    assert stored_files(media_dir) == []


async def test_media_reprocess_regenerates_thumb_only(
    content_modules: None,
    admin: AsyncClient,
    app: FastAPI,
    p: str,
    migrated_db_url: str,
) -> None:
    media = (await upload(admin, p, _png()))[1]["id"]
    before = await sql(
        migrated_db_url,
        "SELECT name, sha256, storage_key FROM media_variant WHERE media_id = :m ORDER BY name",
        m=UUID(str(media)),
    )
    assert [b[0] for b in before] == ["original", "thumb"]
    await sql(
        migrated_db_url,
        "DELETE FROM media_variant WHERE name = 'thumb' AND media_id = :m",
        m=UUID(str(media)),
    )
    report = await reprocess(rt(app), "thumb")
    assert (report.created, report.failed) == (1, 0)
    after = await sql(
        migrated_db_url,
        "SELECT name, sha256, storage_key FROM media_variant WHERE media_id = :m ORDER BY name",
        m=UUID(str(media)),
    )
    assert [a[0] for a in after] == ["original", "thumb"]
    assert after[0] == before[0]
    assert after[1][2].startswith(before[1][2].removesuffix(".webp") + "-r1-")
    assert (await sql(migrated_db_url, "SELECT revision FROM media"))[0] == (1,)
    again = await reprocess(rt(app), "thumb")
    assert (again.created, again.skipped) == (0, 1)
    forced = await reprocess(rt(app), "thumb", force=True)
    assert forced.created == 1
