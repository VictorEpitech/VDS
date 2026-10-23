"""/ws/player : auth par premier message, welcome, schedule.changed, révocation, deux base paths."""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from test_agents import FakeWS
from test_auth import SAME, admin, anon, app, base, clean, editor, p  # noqa: F401
from test_diffusion import (  # noqa: F401
    _content_types,
    _diffusion_clean,
    make_media,
    mk_screen,
)
from test_pairing import _pairing_clean, hdr, pair  # noqa: F401
from test_player_schedule import _entries, schedule_media_sql  # noqa: F401
from vds_sdk.wire import SUBPROTOCOL_PLAYER
from vds_server.ws import player as player_ws


async def open_ws(application: FastAPI, prefix: str, protos: list[str] | None = None) -> FakeWS:
    protocols = [SUBPROTOCOL_PLAYER] if protos is None else protos
    ws = FakeWS(application, f"{prefix}/ws/player", {}, protocols)
    return await ws.open()


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_ws, "CHECK_S", 0.2)


async def paired(admin: AsyncClient, anon: AsyncClient, p: str) -> tuple[str, str]:  # noqa: F811
    _, screen = await mk_screen(admin, p)
    return screen, await pair(admin, anon, p, screen)


async def test_auth_welcome_and_push(
    admin: AsyncClient,  # noqa: F811
    anon: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
    migrated_db_url: str,
) -> None:
    screen, token = await paired(admin, anon, p)
    ws = await open_ws(app, p)
    assert ws.accepted and ws.subprotocol == SUBPROTOCOL_PLAYER
    await ws.send("auth", {"token": token})
    w = await ws.recv()
    assert w is not None and w["type"] == "welcome"
    assert w["data"]["screen_id"] == screen and w["data"]["revision"].startswith("sha256:")
    r = await anon.get(f"{p}/api/v1/player/schedule", headers=hdr(token))  # polling intact
    assert r.status_code == 200 and r.json()["revision"] == w["data"]["revision"]
    # Aucune trame tant que la programmation ne change pas.
    with pytest.raises(asyncio.TimeoutError):
        await ws._next(0.6)
    media = await make_media(migrated_db_url)
    await schedule_media_sql(migrated_db_url, screen, media)
    f = await ws.recv()
    assert f is not None and f["type"] == "schedule.changed"
    r2 = await anon.get(f"{p}/api/v1/player/schedule", headers=hdr(token))
    assert f["data"] == {"revision": r2.json()["revision"]}
    await ws.send("heartbeat", {"revision": f["data"]["revision"], "app_version": "9.9"})
    await ws.close()


async def test_refusals(
    admin: AsyncClient,  # noqa: F811
    anon: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
) -> None:
    ws = await open_ws(app, p, protos=[])
    assert not ws.accepted or await ws.recv() is None
    assert ws.close_code == 4426
    ws = await open_ws(app, p)
    await ws.send("auth", {"token": "vds_dev_inconnu"})
    assert await ws.recv() is None and ws.close_code == 4401
    ws = await open_ws(app, p)
    await ws.send("heartbeat", {})  # pas d'auth d'abord
    assert await ws.recv() is None and ws.close_code == 4400
    # Un jeton d'agent ou le jeton en en-tête ne suffisent pas.
    ws = FakeWS(app, f"{p}/ws/player", {"Authorization": "Bearer vds_dev_x"}, [SUBPROTOCOL_PLAYER])
    await ws.open()
    await ws.send("auth", {"token": "x"})
    assert await ws.recv() is None and ws.close_code == 4401


async def test_auth_timeout(app: FastAPI, p: str, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(player_ws, "AUTH_TIMEOUT_S", 0.2)
    ws = await open_ws(app, p)
    assert await ws.recv() is None and ws.close_code == 4408


async def test_revoked_closes_4410(
    admin: AsyncClient,  # noqa: F811
    anon: AsyncClient,  # noqa: F811
    app: FastAPI,  # noqa: F811
    p: str,  # noqa: F811
) -> None:
    screen, token = await paired(admin, anon, p)
    ws = await open_ws(app, p)
    await ws.send("auth", {"token": token})
    assert (await ws.recv()) is not None
    r = await admin.delete(f"{p}/api/v1/screens/{screen}/device", headers=SAME)
    assert r.status_code == 204
    assert await ws.recv() is None and ws.close_code == 4410
    ws2 = await open_ws(app, p)
    await ws2.send("auth", {"token": token})
    assert await ws2.recv() is None and ws2.close_code == 4410
