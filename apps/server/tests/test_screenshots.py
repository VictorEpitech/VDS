"""Lot I3 : captures d'écran (upload validé, réencodage, stockage hors /media, lecture avec
périmètre en no-store, rétention, purge, configuration), aux deux VDS_BASE_PATH."""
# ruff: noqa: F811

import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from PIL import Image
from test_auth import SAME, admin, anon, app, base, clean, new_client, p  # noqa: F401
from test_diffusion import _content_types  # noqa: F401
from test_pairing import _pairing_clean, hdr  # noqa: F401
from test_perimeter import World, sql, world  # noqa: F401
from test_player_commands import CWorld, _fast, beat, cw, get_cmd, send  # noqa: F401
from vds_server.core.runtime import Runtime


def make_jpeg(size: tuple[int, int] = (2000, 1000), *, exif: bool = False, **kw: Any) -> bytes:
    img = Image.new("RGB", size, (200, 30, 30))
    buf = io.BytesIO()
    if exif:
        meta = Image.Exif()
        meta[0x010E] = "secret-description"  # ImageDescription
        meta[0x013B] = "secret-author"  # Artist
        kw["exif"] = meta
    img.save(buf, "JPEG", **kw)
    return buf.getvalue()


def storage_file(key: str) -> Path:
    return Path(os.environ["VDS_MEDIA_DIR"]) / key


async def upload(
    c: CWorld,
    key: str,
    body: bytes,
    cmd_id: str | None = None,
    ctype: str = "image/jpeg",
    **headers: str,
) -> Any:
    url = f"{c.p}/api/v1/player/screenshots" + (f"/{cmd_id}" if cmd_id else "")
    h = {**hdr(c.tokens[key]), "Content-Type": ctype, **headers}
    return await c.player.post(url, content=body, headers=h)


async def capture(c: CWorld, admin: AsyncClient, key: str = "sA", **kw: Any) -> tuple[str, str]:
    """Commande `screenshot` puis envoi de l'image ; retourne (cmd_id, screenshot_id)."""
    cmd = (await send(admin, c.p, c.screens[key], "screenshot")).json()
    r = await upload(c, key, make_jpeg(**kw), cmd["id"])
    assert r.status_code == 201, r.text
    return cmd["id"], r.json()["screenshot_id"]


async def test_on_demand_capture_reencoded_stored_and_read(
    cw: CWorld, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    raw = make_jpeg(exif=True)
    assert b"secret-description" in raw
    cmd = (await send(admin, p, cw.screens["sA"], "screenshot")).json()
    when = (datetime.now(UTC) - timedelta(seconds=3)).isoformat()
    r = await upload(
        cw,
        "sA",
        raw,
        cmd["id"],
        **{"X-VDS-Captured-At": when, "Content-Disposition": "x; filename=../../e.sh"},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["screenshot_id"]
    done = await get_cmd(admin, p, cmd["id"])
    assert done["status"] == "ok" and done["screenshot_id"] == sid
    # le player rejoue son résultat après coup : ignoré
    ack = await beat(cw, "sA", command_results=[{"cmd_id": cmd["id"], "status": "failed"}])
    assert ack["results_acked"] == [cmd["id"]]
    assert (await get_cmd(admin, p, cmd["id"]))["status"] == "ok"
    # liste et lecture
    lst = (await admin.get(f"{p}/api/v1/screens/{cw.screens['sA']}/screenshots")).json()
    assert [s["id"] for s in lst] == [sid]
    shot = lst[0]
    assert shot["trigger"] == "on_demand" and (shot["width"], shot["height"]) == (1280, 640)
    assert shot["image_url"] == f"/api/v1/screenshots/{sid}/image"
    assert 0 < shot["bytes"] <= 2 * 1024 * 1024
    r = await admin.get(f"{p}{shot['image_url']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["content-security-policy"] == "default-src 'none'"
    assert r.content.startswith(b"\xff\xd8\xff") and len(r.content) == shot["bytes"]
    assert b"secret" not in r.content
    img = Image.open(io.BytesIO(r.content))
    assert max(img.size) <= 1280 and len(img.getexif()) == 0 and "icc_profile" not in img.info
    # stockage : clé fabriquée par le serveur, jamais le nom du client, jamais servie par /media
    (row,) = await sql(migrated_db_url, "SELECT storage_key, sha256, command_id FROM screenshot")
    assert row[0] == f"screenshots/{cw.screens['sA'][:2]}/{cw.screens['sA']}/{sid}.jpg"
    assert "e.sh" not in row[0] and str(row[2]) == cmd["id"]
    assert storage_file(row[0]).read_bytes() == r.content
    assert (await admin.get(f"{p}/media/{sid}")).status_code in (401, 404)
    scr = (await admin.get(f"{p}/api/v1/screens/{cw.screens['sA']}")).json()
    assert scr["last_screenshot"]["id"] == sid


async def test_upload_validation(cw: CWorld, admin: AsyncClient, p: str) -> None:
    cmd = (await send(admin, p, cw.screens["sA"], "screenshot")).json()
    cid = cmd["id"]
    good = make_jpeg((64, 64))
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    # type déclaré / octets réels
    assert (await upload(cw, "sA", good, cid, ctype="image/png")).status_code == 415
    assert (await upload(cw, "sA", good, cid, ctype="text/plain")).status_code == 415
    assert (await upload(cw, "sA", png.getvalue(), cid)).status_code == 415  # PNG sous JPEG
    assert (await upload(cw, "sA", b"", cid)).status_code == 415
    assert (await upload(cw, "sA", b"<?php evil ?>", cid)).status_code == 415
    r = await upload(cw, "sA", good[: len(good) // 2], cid)  # image tronquée
    assert (r.status_code, r.json()["code"]) == (415, "screenshot.invalid_image")
    # taille : 2 Mo max, contrôlée en flux
    big = b"\xff\xd8\xff" + b"0" * (2 * 1024 * 1024)
    r = await upload(cw, "sA", big, cid)
    assert (r.status_code, r.json()["code"]) == (413, "screenshot.too_large")
    # bombe de décompression : petit fichier, dimensions énormes (refusé avant décodage)
    bomb = make_jpeg((9000, 9000))
    assert len(bomb) < 2 * 1024 * 1024
    r = await upload(cw, "sA", bomb, cid)
    assert (r.status_code, r.json()["code"]) == (413, "screenshot.too_many_pixels")
    r = await upload(cw, "sA", good, cid, **{"X-VDS-Captured-At": "pas une date"})
    assert (r.status_code, r.json()["code"]) == (422, "screenshot.captured_at_invalid")
    # authentification : jeton d'appareil obligatoire
    url = f"{p}/api/v1/player/screenshots/{cid}"
    r = await cw.player.post(url, content=good, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 403  # sans jeton : le contrôle de même origine refuse d'abord
    assert (await admin.post(url, content=good, headers={**SAME, "Content-Type": "image/jpeg"})
            ).status_code == 401  # fmt: skip
    # rien n'a été enregistré, la commande attend toujours
    assert (await get_cmd(admin, p, cid))["status"] == "queued"
    assert (await admin.get(f"{p}/api/v1/screens/{cw.screens['sA']}/screenshots")).json() == []
    # un dessin en couleurs indexées ou en niveaux de gris est accepté et normalisé
    gray = io.BytesIO()
    Image.new("L", (100, 50)).save(gray, "JPEG")
    assert (await upload(cw, "sA", gray.getvalue(), cid)).status_code == 201


async def test_command_binding_409(cw: CWorld, admin: AsyncClient, p: str) -> None:
    good = make_jpeg((64, 64))
    shot = (await send(admin, p, cw.screens["sA"], "screenshot")).json()
    reload_ = (await send(admin, p, cw.screens["sA"], "reload")).json()
    other = (await send(admin, p, cw.screens["sB"], "screenshot")).json()
    for cid in (reload_["id"], other["id"], str(uuid4())):  # autre nom, autre appareil, inconnue
        r = await upload(cw, "sA", good, cid)
        assert (r.status_code, r.json()["code"]) == (409, "screenshot.command_invalid"), cid
    assert (await upload(cw, "sA", good, shot["id"])).status_code == 201
    r = await upload(cw, "sA", good, shot["id"])  # déjà terminée
    assert (r.status_code, r.json()["code"]) == (409, "screenshot.command_invalid")
    # expirée côté serveur : plus acceptée
    s2 = (await send(admin, p, cw.screens["sA"], "screenshot")).json()
    await sql(
        os.environ["VDS_TEST_DATABASE_URL"],
        "UPDATE player_command SET expires_at = now() - interval '1 s' WHERE id = :i",
        i=UUID(s2["id"]),
    )
    assert (await upload(cw, "sA", good, s2["id"])).status_code == 409


async def test_periodic_disabled_by_default_then_rate_limited(
    cw: CWorld, admin: AsyncClient, p: str
) -> None:
    good = make_jpeg((64, 64))
    screen = cw.screens["sA"]
    r = await upload(cw, "sA", good)
    assert (r.status_code, r.json()["code"]) == (409, "screenshot.disabled")
    doc = (await cw.player.get(f"{p}/api/v1/player/schedule", headers=hdr(cw.tokens["sA"]))).json()
    assert "screenshot" not in doc  # absent = désactivée
    cfg = f"{p}/api/v1/config/screen/{screen}/core/player.screenshot_interval_min"
    for bad in (1, 4, 1441, -5, "x"):
        r = await admin.put(cfg, json={"value": bad}, headers=SAME)
        assert r.status_code == 422, bad
    assert (await admin.put(cfg, json={"value": 5}, headers=SAME)).status_code == 204
    doc = (await cw.player.get(f"{p}/api/v1/player/schedule", headers=hdr(cw.tokens["sA"]))).json()
    assert doc["screenshot"] == {"interval_min": 5}
    r = await upload(cw, "sA", good)
    assert r.status_code == 201, r.text
    lst = (await admin.get(f"{p}/api/v1/screens/{screen}/screenshots")).json()
    assert lst[0]["trigger"] == "periodic"
    r = await upload(cw, "sA", good)  # 1 capture / min / appareil
    assert (r.status_code, r.json()["code"]) == (429, "screenshot.rate_limited")
    assert int(r.headers["Retry-After"]) >= 1
    assert (await admin.put(cfg, json={"value": 0}, headers=SAME)).status_code == 204
    doc = (await cw.player.get(f"{p}/api/v1/player/schedule", headers=hdr(cw.tokens["sA"]))).json()
    assert "screenshot" not in doc
    # niveaux : jamais au parc entier d'un clic (organisation / site refusés)
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    r = await admin.put(
        f"{p}/api/v1/config/organization/{org}/core/player.screenshot_interval_min",
        json={"value": 10},
        headers=SAME,
    )
    assert (r.status_code, r.json()["code"]) == (422, "config.scope_not_allowed")


async def test_config_defaults_and_bounds(cw: CWorld, admin: AsyncClient, p: str) -> None:
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    vals = (await admin.get(f"{p}/api/v1/config/organization/{org}/core")).json()["values"]
    assert vals["screenshots.retention_h"]["value"] == 24
    assert vals["screenshots.max_per_screen"]["value"] == 10
    assert vals["player.screenshot_interval_min"]["value"] == 0
    base_url = f"{p}/api/v1/config/organization/{org}/core"
    for key, bad in (("retention_h", 0), ("retention_h", 169), ("max_per_screen", 51)):
        r = await admin.put(f"{base_url}/screenshots.{key}", json={"value": bad}, headers=SAME)
        assert r.status_code == 422, (key, bad)
    ok = await admin.put(f"{base_url}/screenshots.retention_h", json={"value": 168}, headers=SAME)
    assert ok.status_code == 204


# --- lecture et périmètre (lot J) -----------------------------------------------------------
async def test_read_requires_session_and_perimeter(cw: CWorld, admin: AsyncClient, p: str) -> None:
    _, sid_a = await capture(cw, admin, "sA")
    _, sid_b = await capture(cw, admin, "sB")
    _, sid_a1 = await capture(cw, admin, "sA1")
    anon_c = new_client(cw.app)
    img = f"{p}/api/v1/screenshots"
    assert (await anon_c.get(f"{img}/{sid_a}/image")).status_code == 401
    assert (await cw.player.get(f"{img}/{sid_a}/image", headers=hdr(cw.tokens["sA"]))
            ).status_code == 401  # fmt: skip  # un jeton d'appareil ne lit pas les captures
    ed_a, ed_a1, ed_none, ed_org = (cw.clients[k] for k in ("ed_A", "ed_A1", "ed_none", "ed_org"))
    sa, sb = cw.screens["sA"], cw.screens["sB"]
    assert (await ed_a.get(f"{img}/{sid_a}/image")).status_code == 200
    assert (await ed_a.get(f"{img}/{sid_a1}/image")).status_code == 200  # groupe du site
    assert (await ed_a1.get(f"{img}/{sid_a1}/image")).status_code == 200
    assert (await ed_org.get(f"{img}/{sid_b}/image")).status_code == 200
    for who, sid in ((ed_a, sid_b), (ed_a1, sid_a), (ed_none, sid_a), (ed_none, sid_b)):
        r = await who.get(f"{img}/{sid}/image")
        assert r.status_code == 404 and r.json()["code"] == "screenshot.not_found", sid
        assert "ZZ" not in r.text
    r = await ed_a.get(f"{p}/api/v1/screens/{sb}/screenshots")
    assert r.status_code == 404 and "ZZ" not in r.text and sid_b not in r.text
    assert (await ed_none.get(f"{p}/api/v1/screens/{sa}/screenshots")).status_code == 404
    assert [s["id"] for s in (await ed_a.get(f"{p}/api/v1/screens/{sa}/screenshots")).json()] == [
        sid_a
    ]
    # la fiche écran ne montre que les captures visibles
    r = await ed_a.get(f"{p}/api/v1/screens")
    assert sid_b not in r.text and sid_a in r.text
    r = await ed_none.get(f"{p}/api/v1/screens")
    assert sid_a not in r.text and sid_b not in r.text
    # consultation non auditée en V1
    rows = await sql(os.environ["VDS_TEST_DATABASE_URL"], "SELECT action FROM audit_log")
    assert not [r_ for r_ in rows if "screenshot" in str(r_[0])]


# --- rétention, plafond, purge ---------------------------------------------------------------
async def test_purge_retention_and_cap_delete_files(
    cw: CWorld, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    rt: Runtime = cw.app.state.runtime
    org = (await admin.get(f"{p}/api/v1/organization")).json()["id"]
    cfg = f"{p}/api/v1/config/organization/{org}/core/screenshots.max_per_screen"
    assert (await admin.put(cfg, json={"value": 2}, headers=SAME)).status_code == 204
    ids = [(await capture(cw, admin, "sA", size=(64, 64)))[1] for _ in range(3)]
    keys = {
        str(r[0]): str(r[1])
        for r in await sql(migrated_db_url, "SELECT id, storage_key FROM screenshot")
    }
    assert len(keys) == 3 and all(storage_file(k).exists() for k in keys.values())
    assert await rt.capture_sweeper.purge() == 1  # plafond : le plus ancien part
    left = {str(r[0]) for r in await sql(migrated_db_url, "SELECT id FROM screenshot")}
    assert left == set(ids[1:]) and not storage_file(keys[ids[0]]).exists()
    assert storage_file(keys[ids[1]]).exists()
    # rétention : au-delà de 24 h (défaut), fichier puis ligne
    assert await rt.capture_sweeper.purge(datetime.now(UTC) + timedelta(hours=23)) == 0
    assert await rt.capture_sweeper.purge(datetime.now(UTC) + timedelta(hours=25)) == 2
    assert await sql(migrated_db_url, "SELECT 1 FROM screenshot") == []
    assert not any(storage_file(k).exists() for k in keys.values())


async def test_screen_delete_removes_captures(
    cw: CWorld, admin: AsyncClient, p: str, migrated_db_url: str
) -> None:
    await capture(cw, admin, "sA", size=(64, 64))
    (row,) = await sql(migrated_db_url, "SELECT storage_key FROM screenshot")
    assert storage_file(row[0]).exists()
    assert (await admin.delete(f"{p}/api/v1/screens/{cw.screens['sA']}", headers=SAME)
            ).status_code == 204  # fmt: skip
    assert await sql(migrated_db_url, "SELECT 1 FROM screenshot") == []
    assert not storage_file(row[0]).exists()


@pytest.mark.parametrize("who", ["ed_A", "ed_none"])
async def test_no_cross_perimeter_leak_in_screens(cw: CWorld, admin: AsyncClient, who: str) -> None:
    """Le nom et l'identifiant de l'écran B ne fuient dans aucune réponse commandes/captures."""
    p = cw.p
    cmd_b, shot_b = await capture(cw, admin, "sB")
    c = cw.clients[who]
    sb = cw.screens["sB"]
    forbidden = ["Ecran-ZZB", sb, cmd_b, shot_b]
    urls = [
        f"{p}/api/v1/screens/{sb}/commands",
        f"{p}/api/v1/screens/{sb}/screenshots",
        f"{p}/api/v1/screenshots/{shot_b}/image",
        f"{p}/api/v1/player-commands?ids={cmd_b}",
        f"{p}/api/v1/screens",
        f"{p}/api/v1/fleet/summary",
        f"{p}/api/v1/fleet/playback",
    ]
    for url in urls:
        r = await c.get(url)
        assert r.status_code in (200, 404), (url, r.status_code)
        for token in forbidden:
            assert token not in r.text, (url, token)
    post = await send(c, p, sb, "screenshot")
    assert post.status_code == 404
    for token in forbidden:
        assert token not in post.text
