"""Octets des médias : envoi, sha256, types, lecture partielle, accès (deux VDS_BASE_PATH)."""

import hashlib
import io
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from test_auth import (  # noqa: F401 - fixtures partagées
    SAME,
    admin,
    anon,
    app,
    base,
    clean,
    editor,
    new_client,
    p,
)
from test_diffusion import mk_playlist, mk_screen
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401
from test_player_schedule import mk_schedule, schedule_media_sql
from vds_server.diffusion.media_files import ByteRange, parse_range


def _png() -> bytes:
    """PNG valide (content-types intégrés actifs : l'image est décodée) + octets de bourrage."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue() + bytes(range(256)) * 8


PNG = _png()
BAD_PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8  # en-tête PNG, octets illisibles
PDF = b"%PDF-1.7\n" + b"x" * 100
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'/>"


@pytest.fixture(autouse=True)
def media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VDS_MEDIA_DIR", str(tmp_path / "media"))
    return tmp_path / "media"


@pytest.fixture(autouse=True)
async def _clean_media(migrated_db_url: str) -> None:
    engine = create_async_engine(migrated_db_url, poolclass=pool.NullPool)
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE media CASCADE"))
    await engine.dispose()


def stored_files(root: Path) -> list[Path]:
    return [f for f in root.rglob("*") if f.is_file()]


async def upload(
    c: AsyncClient,
    p: str,
    data: bytes = PNG,
    *,
    mime: str = "image/png",
    name: str = "a.png",
    sha256: str | None = None,
) -> tuple[int, dict[str, object]]:
    form = {"sha256": sha256} if sha256 else {}
    r = await c.post(
        f"{p}/api/v1/media", files={"file": (name, data, mime)}, data=form, headers=SAME
    )
    return r.status_code, r.json()


async def test_upload_and_full_read(admin: AsyncClient, p: str, media_dir: Path) -> None:
    digest = hashlib.sha256(PNG).hexdigest()
    status, body = await upload(admin, p, sha256=digest)
    assert status == 201, body
    assert body["sha256"] == digest
    assert body["bytes"] == len(PNG)
    assert body["kind"] == "image"
    assert body["status"] == "ready"
    mid = str(body["id"])
    assert (media_dir / mid[:2] / mid / "original.png").is_file()

    r = await admin.get(f"{p}/media/{mid}/original")
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["etag"] == f'"{digest}"'

    r = await admin.get(f"{p}/media/{mid}/original", headers={"If-None-Match": f'"{digest}"'})
    assert r.status_code == 304

    r = await admin.get(f"{p}/api/v1/media/{mid}")
    assert r.json()["variants"][0]["name"] == "original"


async def test_partial_reads(admin: AsyncClient, p: str) -> None:
    _, body = await upload(admin, p)
    url = f"{p}/media/{body['id']}/original"
    r = await admin.get(url, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == PNG[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(PNG)}"
    assert r.headers["content-length"] == "10"
    r = await admin.get(url, headers={"Range": "bytes=-5"})
    assert (r.status_code, r.content) == (206, PNG[-5:])
    r = await admin.get(url, headers={"Range": f"bytes={len(PNG) - 3}-"})
    assert (r.status_code, r.content) == (206, PNG[-3:])
    r = await admin.get(url, headers={"Range": f"bytes={len(PNG)}-"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(PNG)}"
    r = await admin.head(url, headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["content-length"] == "4"
    assert r.content == b""


async def test_sha256_mismatch_rejected_and_nothing_kept(
    admin: AsyncClient, p: str, media_dir: Path
) -> None:
    status, body = await upload(admin, p, sha256="0" * 64)
    assert (status, body["code"]) == (422, "media.sha256_mismatch")
    status, body = await upload(admin, p, sha256="zz")
    assert (status, body["code"]) == (422, "media.sha256_invalid")
    assert stored_files(media_dir) == []
    assert (await admin.get(f"{p}/api/v1/media")).json() == []


@pytest.mark.parametrize(
    ("data", "mime", "name", "code"),
    [
        (b"hello world", "text/plain", "a.txt", "media.type_invalid"),
        (SVG, "image/svg+xml", "a.svg", "media.type_invalid"),
        (b"MZ\x90\x00" + b"\0" * 64, "image/png", "evil.png", "media.type_invalid"),
        (PDF, "image/png", "a.png", "media.type_mismatch"),
        (b"", "image/png", "a.png", "media.empty"),
    ],
)
async def test_invalid_types_rejected(
    admin: AsyncClient, p: str, data: bytes, mime: str, name: str, code: str
) -> None:
    status, body = await upload(admin, p, data, mime=mime, name=name)
    assert status in (415, 422)
    assert body["code"] == code


async def test_pdf_is_document_and_octet_stream_accepted(admin: AsyncClient, p: str) -> None:
    status, body = await upload(admin, p, PDF, mime="application/octet-stream", name="../d.pdf")
    assert status == 201
    assert body["kind"] == "document"
    assert body["mime"] == "application/pdf"
    assert body["original_filename"] == "d.pdf"


async def test_access_control(app: FastAPI, admin: AsyncClient, p: str) -> None:
    _, body = await upload(admin, p)
    url = f"{p}/media/{body['id']}/original"
    async with new_client(app) as other:
        assert (await other.get(url)).status_code == 401
        r = await other.post(
            f"{p}/api/v1/media", files={"file": ("a.png", PNG, "image/png")}, headers=SAME
        )
        assert r.status_code == 401


async def test_device_reader_hook(app: FastAPI, admin: AsyncClient, p: str) -> None:
    _, body = await upload(admin, p)
    url = f"{p}/media/{body['id']}/original"

    async def reader(request: Request) -> bool:
        return request.headers.get("authorization") == "Bearer vds_dev_test"

    app.state.runtime.media_access.register_reader(reader)
    auth = {"Authorization": "Bearer vds_dev_test"}
    async with new_client(app) as device:
        assert (await device.get(url)).status_code == 401
        r = await device.get(url, headers=auth)
        assert (r.status_code, r.content) == (200, PNG)
        r = await device.post(  # un jeton d'appareil ne permet pas d'envoyer
            f"{p}/api/v1/media", files={"file": ("a.png", PNG, "image/png")}, headers=auth
        )
        assert r.status_code == 403  # Bearer n'exempte plus le contrôle de même origine


async def test_real_device_token_reads_media(
    app: FastAPI, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    _, body = await upload(admin, p)
    url = f"{p}/media/{body['id']}/original"
    _, screen = await mk_screen(admin, p)
    async with new_client(app) as device:
        token = await pair(admin, device, p, screen)
        # hors programmation de l'écran : refusé
        assert (await device.get(url, headers=hdr(token))).status_code == 403
        await schedule_media_sql(migrated_db_url, screen, UUID(str(body["id"])))
        r = await device.get(url, headers=hdr(token))
        assert (r.status_code, r.content) == (200, PNG)
        assert (await device.get(url, headers=hdr("vds_dev_inconnu"))).status_code == 401
        assert (await device.get(url)).status_code == 401
        r = await admin.delete(f"{p}/api/v1/screens/{screen}/device", headers=SAME)
        assert r.status_code == 204
        assert (await device.get(url, headers=hdr(token))).status_code == 401


async def test_unknown_and_malicious_paths(admin: AsyncClient, p: str) -> None:
    _, body = await upload(admin, p)
    mid = body["id"]
    for path in (
        f"/media/{mid}/poster",
        f"/media/{mid}/..%2f..%2fetc",
        f"/media/{mid}/Original",
        "/media/not-a-uuid/original",
        "/media/00000000-0000-0000-0000-000000000000/original",
    ):
        assert (await admin.get(f"{p}{path}")).status_code == 404, path


def test_parse_range() -> None:
    assert parse_range(None, 100) is None
    assert parse_range("bytes=0-9", 100) == ByteRange(0, 9)
    assert parse_range("bytes=90-500", 100) == ByteRange(90, 99)
    assert parse_range("bytes=-10", 100) == ByteRange(90, 99)
    assert parse_range("bytes=-500", 100) == ByteRange(0, 99)
    assert parse_range("bytes=100-", 100) == "invalid"
    assert parse_range("bytes=9-3", 100) == "invalid"
    assert parse_range("bytes=0-1,5-6", 100) is None
    assert parse_range("items=0-1", 100) is None
    assert parse_range("bytes=-", 100) is None


@pytest.fixture
async def content_modules(app: FastAPI) -> None:
    for mid in ("content.image", "content.pdf"):
        await app.state.runtime.registry.enable(mid)


def _real_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(buf, "PNG")
    return buf.getvalue()


def _real_pdf(pages: int = 2) -> bytes:
    import io

    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(200, 100)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


async def test_image_processed_by_module(content_modules: None, admin: AsyncClient, p: str) -> None:
    status, body = await upload(admin, p, _real_png())
    assert status == 201, body
    assert body["status"] == "ready"
    assert (body["width"], body["height"]) == (40, 20)
    r = await admin.get(f"{p}/api/v1/media/{body['id']}")
    assert {v["name"] for v in r.json()["variants"]} == {"original", "thumb"}
    t = await admin.get(f"{p}/media/{body['id']}/thumb")
    assert t.status_code == 200
    assert t.headers["content-type"] == "image/jpeg"


async def test_pdf_pages_processed_by_module(
    content_modules: None, admin: AsyncClient, p: str
) -> None:
    status, body = await upload(admin, p, _real_pdf(2), mime="application/pdf", name="a.pdf")
    assert status == 201, body
    assert body["status"] == "ready"
    assert body["page_count"] == 2
    r = await admin.get(f"{p}/api/v1/media/{body['id']}")
    assert {v["name"] for v in r.json()["variants"]} == {"original", "page-1", "page-2"}


async def test_processing_failure_marks_failed(
    content_modules: None, admin: AsyncClient, p: str
) -> None:
    status, body = await upload(admin, p, BAD_PNG)
    assert status == 201, body
    assert body["status"] == "failed"
    assert body["error"] == "content.image.invalid"
    r = await admin.get(f"{p}/api/v1/media/{body['id']}")
    assert [v["name"] for v in r.json()["variants"]] == ["original"]


async def test_pdf_pages_in_player_schedule(
    content_modules: None, admin: AsyncClient, app: FastAPI, p: str
) -> None:
    _, screen = await mk_screen(admin, p)
    _, mine = await upload(admin, p, _real_pdf(3), mime="application/pdf", name="a.pdf")
    _, other = await upload(admin, p, _real_pdf(1), mime="application/pdf", name="b.pdf")
    pl = await mk_playlist(admin, p)
    r = await admin.post(
        f"{p}/api/v1/playlists/{pl}/items",
        json={"content_type": "content.pdf", "props": {"media": mine["id"]}},
        headers=SAME,
    )
    assert r.status_code == 201, r.text
    await mk_schedule(admin, p, pl, screen)
    async with new_client(app) as player:
        token = await pair(admin, player, p, screen)
        doc = (await player.get(f"{p}/api/v1/player/schedule", headers=hdr(token))).json()
        item = next(iter(doc["playlists"].values()))["items"][0]
        pages = [f"media/{mine['id']}/page-{n}" for n in (1, 2, 3)]
        assert item["props"]["pages"] == pages and item["props"]["page_count"] == 3
        assert item["assets"] == pages and item["duration_ms"] == 3 * 8000
        assert all(k in doc["assets"] and doc["assets"][k]["mime"] == "image/jpeg" for k in pages)
        assert '"/' not in json.dumps(doc) and "http" not in json.dumps(doc)
        # l'URL relative se résout contre l'URL serveur (préfixe compris)
        ok = await player.get(f"{p}/{pages[1]}", headers=hdr(token))
        assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
        assert ok.headers["etag"].strip('"') == doc["assets"][pages[1]]["sha256"]
        no = await player.get(f"{p}/media/{other['id']}/page-1", headers=hdr(token))
        assert no.status_code == 403 and no.json()["code"] == "media.forbidden"
