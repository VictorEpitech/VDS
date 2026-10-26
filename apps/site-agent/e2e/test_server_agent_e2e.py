"""E2E d'intégration lot 3 : VRAI serveur (uvicorn TLS + PostgreSQL 17) + VRAI agent.

Pilotes = vrais modules (power.samsung-ws, power.adb, power.wol) face à de fausses TV
(WSS, TCP, UDP) ; faux SMTP pour notify.mail. Joué sous VDS_BASE_PATH `/` et `/espace/affichage`.

Lancer : `make e2e-agent` (conteneur PG jetable, supprimé en fin de run).
"""

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine
from vds_agent import runtime as agent_runtime
from vds_agent.runtime import Agent
from vds_agent.settings import AgentSettings
from vds_agent.store import Store
from vds_agent.testing import load_fakes

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/site-agent/tests"))
from helpers import make_cert  # noqa: E402

ADMIN_EMAIL = "admin@e2e.vds.test"
ADMIN_PASSWORD = "e2e-" + secrets.token_urlsafe(12)
COMBOS = [("root", "", 4291), ("subpath", "/espace/affichage", 4292)]
PG_PORT = 4390
PG_NAME = "vds-e2e-agent-pg"
APK_BYTES = b"PK\x03\x04e2e-fake-apk"
TV_LAN = "192.168.1.50"  # adresse LAN acceptée par le serveur ; redirigée vers le faux ADB


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        check=check,
        capture_output=True,
        text=True,
    )


def _psql(pw: str, db: str, sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec", "-e", f"PGPASSWORD={pw}", PG_NAME,
        "psql", "-h", "127.0.0.1", "-U", "vds", "-d", db, "-tAc", sql,
        check=False,
    )  # fmt: skip


@pytest.fixture(scope="session")
def pg() -> Iterator[str]:
    """PostgreSQL 17 jetable (mot de passe aléatoire) ; supprimé en fin de session."""
    pw = secrets.token_urlsafe(18)
    _docker("rm", "-f", "-v", PG_NAME, check=False)
    _docker(
        "run", "-d", "--name", PG_NAME,
        "-e", f"POSTGRES_PASSWORD={pw}", "-e", "POSTGRES_USER=vds", "-e", "POSTGRES_DB=postgres",
        "-p", f"127.0.0.1:{PG_PORT}:5432", "postgres:17",
    )  # fmt: skip
    try:
        for _ in range(60):
            if _psql(pw, "postgres", "select 1").stdout.strip() == "1":
                break
            time.sleep(1)
        else:
            pytest.fail("PostgreSQL ne démarre pas")
        yield pw
    finally:
        _docker("rm", "-f", "-v", PG_NAME, check=False)


async def poll(cond: Callable[[], Any], what: str, timeout: float = 30) -> Any:  # noqa: ASYNC109
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = cond()
        if asyncio.iscoroutine(v):
            v = await v
        if v:
            return v
        await asyncio.sleep(0.2)
    pytest.fail(f"délai dépassé : {what}")


class Server:
    def __init__(self, tmp: Path, base: str, port: int, db_url: str, env: dict[str, str]) -> None:
        self.base, self.port, self.db_url = base, port, db_url
        self.ca, self.key = make_cert(tmp)
        self.origin = f"https://127.0.0.1:{port}"
        self.url = self.origin + base
        media = tmp / "media"
        media.mkdir()
        apk_dir = tmp / "apk"
        apk_dir.mkdir()
        (apk_dir / "vds-player-7.apk").write_bytes(APK_BYTES)
        (apk_dir / "apk.json").write_text(
            json.dumps([{
                "package": "app.vds.player", "version_code": 7, "version_name": "1.0.7",
                "sha256": hashlib.sha256(APK_BYTES).hexdigest(), "size": len(APK_BYTES),
                "signer_sha256": "0" * 64, "file": "vds-player-7.apk",
            }]),
            encoding="utf-8",
        )  # fmt: skip
        self.env = {
            **os.environ,
            **env,
            "VDS_BASE_PATH": base or "/",
            "VDS_DATABASE_URL": db_url,
            "VDS_PUBLIC_ORIGIN": self.origin,
            "VDS_MODULES_DIR": str(ROOT / "modules"),
            "VDS_MEDIA_DIR": str(media),
            "VDS_APK_DIR": str(apk_dir),
            "VDS_SECRET_KEY": "e2e-" + "k" * 40,
            "VDS_PLAYER_DIR": str(tmp),
            "VDS_ADMIN_EMAIL": ADMIN_EMAIL,
            "VDS_ADMIN_PASSWORD": ADMIN_PASSWORD,
        }
        self.proc: subprocess.Popen[bytes] | None = None
        self.log = tmp / "server.log"

    def start(self) -> None:
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "vds_server.migrate"],
            env=self.env, check=True, cwd=ROOT, capture_output=True,
        )  # fmt: skip
        self.proc = subprocess.Popen(  # noqa: S603
            [
                sys.executable, "-m", "uvicorn", "vds_server.main:app_factory", "--factory",
                "--host", "127.0.0.1", "--port", str(self.port),
                "--ssl-keyfile", self.key, "--ssl-certfile", self.ca,
            ],
            env=self.env, cwd=ROOT, stdout=self.log.open("ab"), stderr=subprocess.STDOUT,
        )  # fmt: skip

    def stop(self) -> None:
        if self.proc:
            mark = self.log.stat().st_size if self.log.exists() else 0
            self.proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(15)
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc = None
            # arrêt propre : ni MissingGreenlet ni 1011 pendant la fermeture (écoute LISTEN annulée)
            out = self.log.read_bytes()[mark:].decode(errors="replace")
            assert "MissingGreenlet" not in out and "1011" not in out, out[-2000:]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.url, verify=self.ca, headers={"Origin": self.origin}, timeout=20
        )

    async def wait_ready(self) -> None:
        async def ok() -> bool:
            try:
                async with self.client() as c:
                    return (await c.get("/health")).status_code == 200
            except httpx.HTTPError:
                return False

        await poll(ok, "serveur prêt", 60)

    async def sql(self, query: str, **params: Any) -> list[Any]:
        engine = create_async_engine(self.db_url, poolclass=pool.NullPool)
        async with engine.begin() as c:
            res = await c.execute(text(query), params)
            rows = list(res.all()) if res.returns_rows else []
        await engine.dispose()
        return rows


@pytest.fixture(params=COMBOS, ids=[c[0] for c in COMBOS])
async def stack(
    request: pytest.FixtureRequest, pg: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    name, base, port = request.param
    dbname = f"vds_{name}_{secrets.token_hex(3)}"
    assert _psql(pg, "postgres", f"CREATE DATABASE {dbname}").returncode == 0  # noqa: S608
    notify = _load(ROOT / "modules/notify.mail/tests/fakes.py", "notify_mail_fakes_e2e")
    smtp = notify.FakeSmtp(mode="starttls").start()
    cert = tmp_path / "smtp-ca.pem"
    cert.write_text(smtp.cert_pem)
    srv = Server(
        tmp_path, base, port,
        f"postgresql+asyncpg://vds:{pg}@127.0.0.1:{PG_PORT}/{dbname}",
        {**smtp.env(), "SSL_CERT_FILE": str(cert)},
    )  # fmt: skip
    srv.start()
    monkeypatch.setattr(agent_runtime, "SCHEDULE_TICK_S", 0.3)
    try:
        await srv.wait_ready()
        # notify.mail est un module serveur non requis : activé à chaud par la route admin (audité).
        async with srv.client() as c:
            r = await c.post(
                "/api/v1/m/auth.local/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            )
            assert r.status_code == 200, r.text
            r = await c.put("/api/v1/registry/modules/notify.mail/state", json={"state": "enabled"})
            assert r.status_code == 200 and r.json()["state"] == "enabled", r.text
        yield {"srv": srv, "smtp": smtp, "tmp": tmp_path}
    finally:
        srv.stop()
        smtp.stop()
        if srv.log.exists():
            print(srv.log.read_text()[-3000:])  # noqa: T201 - diagnostic en cas d'échec


async def test_server_agent_full_flow(stack: dict[str, Any]) -> None:
    srv: Server = stack["srv"]
    smtp = stack["smtp"]
    tmp: Path = stack["tmp"]
    fakes_adb = load_fakes("power.adb")
    fakes_sam = load_fakes("power.samsung-ws")
    fakes_wol = load_fakes("power.wol")

    async with (
        fakes_adb.FakeAdbTv(mode="auth") as adb,
        fakes_sam.FakeSamsungTv(mode="deny") as sam,
        fakes_wol.UdpCapture() as udp,
        srv.client() as admin,
    ):
        # --- 1. console : connexion, site, écran, agent (jeton remis une seule fois) ------------
        r = await admin.post(
            "/api/v1/m/auth.local/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert r.status_code == 200, r.text
        r = await admin.post("/api/v1/sites", json={"name": "Moulins", "slug": "moulins"})
        site = r.json()["id"]
        r = await admin.post("/api/v1/screens", json={"site_id": site, "name": "Hall"})
        screen = r.json()["id"]
        made = await admin.post(f"/api/v1/sites/{site}/agents", json={"name": "Pi Moulins"})
        assert made.status_code == 201, made.text
        token, agent_id = made.json()["token"], made.json()["agent"]["id"]
        assert token.startswith("vds_agt_")
        listed = (await admin.get(f"/api/v1/sites/{site}/agents")).json()
        assert "token" not in listed[0]

        async def cfg(level: str, target: str, module: str, key: str, value: object) -> None:
            r = await admin.put(
                f"/api/v1/config/{level}/{target}/{module}/{key}", json={"value": value}
            )
            assert r.status_code == 204, (key, r.text)

        cascade = ["power.samsung-ws", "power.adb", "power.wol"]
        await cfg("site", site, "core", "power.cascade", cascade)
        await cfg("site", site, "core", "power.verify_delay_s", 0)
        await cfg("site", site, "core", "power.retries", 1)
        await cfg("screen", screen, "power.samsung-ws", "host", "127.0.0.1")
        await cfg("screen", screen, "power.samsung-ws", "port", sam.ws_port)
        await cfg("screen", screen, "power.adb", "host", "127.0.0.1")
        await cfg("screen", screen, "power.adb", "port", adb.port)
        await cfg("screen", screen, "power.adb", "timeout_ms", 500)
        await cfg("screen", screen, "power.wol", "mac", "02:00:00:00:00:01")
        await cfg("screen", screen, "power.wol", "broadcast", "127.0.0.1")
        await cfg("screen", screen, "power.wol", "port", udp.port)

        # --- 2. agent réel : /ws/agent (WSS, jeton, sous-protocole) + plan via agent/plan -----------
        shift = [timedelta(0)]
        settings = AgentSettings(
            server_url=srv.url, token=token, data_dir=tmp / "agent",
            modules_dir=ROOT / "modules", ca_file=srv.ca,
        )  # fmt: skip
        agent = Agent(
            settings,
            Store(settings.data_dir),
            heartbeat_s=1.0,
            clock=lambda: datetime.now(UTC) + shift[0],
        )
        task = asyncio.create_task(agent.run())
        try:

            async def seen() -> Any:
                rows = await srv.sql(
                    "SELECT last_seen_at FROM site_agent WHERE id = :i", i=agent_id
                )
                return rows and rows[0][0]

            await poll(seen, "agent connecté (/ws/agent)")
            await poll(lambda: agent.plan.get("screens"), "plan reçu (agent/plan)")
            entry = agent.screen_entry(screen)
            assert entry and entry["cascade"] == cascade
            assert set(entry["drivers"]) == set(cascade)
            assert agent.plan_etag and agent.plan_etag.strip('"') == agent.plan["revision"]

            async def command(state: str) -> dict[str, Any]:
                body = {
                    "name": "power.set",
                    "screen_id": screen,
                    "args": {
                        "state": state,
                        "reason": "manual",
                        "request_id": secrets.token_hex(8),
                    },
                }
                url = f"/api/v1/sites/{site}/agents/{agent_id}/commands"
                r = await admin.post(url, json=body)
                assert r.status_code == 202, r.text
                cid = r.json()["id"]

                async def done() -> Any:
                    row = next(x for x in (await admin.get(url)).json() if x["id"] == cid)
                    return None if row["status"] in ("queued", "sent") else row

                return dict(await poll(done, f"résultat power.set {state}"))

            async def pstate() -> dict[str, Any] | None:
                rows = await srv.sql(
                    "SELECT desired, observed, driver, verified, last_code FROM power_state"
                    " WHERE screen_id = :s",
                    s=screen,
                )
                return dict(rows[0]._mapping) if rows else None

            async def pactions() -> list[Any]:
                return await srv.sql(
                    "SELECT state, reason, ok, verified, driver, code, origin FROM power_action"
                    " WHERE screen_id = :s AND reason = 'manual' ORDER BY at",
                    s=screen,
                )

            async def incidents(state: str) -> list[dict[str, Any]]:
                r = await admin.get(
                    "/api/v1/incidents", params={"state": state, "kind": "power.failed"}
                )
                return list(r.json())

            # --- 3. bascule : Samsung refuse (non appairé) -> ADB prend le relais ---------------------
            res = await command("on")
            assert res["status"] == "ok", res
            assert res["result"]["driver"] == "power.adb" and res["result"]["verified"] is True
            assert adb.wakefulness == "Awake" and not udp.packets
            assert await incidents("open") == []

            async def observed_on() -> Any:
                st = await pstate()
                return st and st["observed"] == "on" and st

            st = await poll(observed_on, "power_state observé = on")
            assert (st["desired"], st["driver"], st["verified"]) == ("on", "power.adb", True)
            first = (await pactions())[
                0
            ]  # commandes manuelles seules (le planning de l'agent est journalisé à part)
            assert first[:6] == ("on", "manual", True, True, "power.adb", None)
            assert first[6] == f"agent:{site}"

            # --- 4. tout échoue (ADB muet, WoL ne sait pas éteindre) -> incident ouvert + mail ----------
            adb.mode = "hang"
            res = await command("off")
            assert res["status"] == "failed", res
            await poll(lambda: incidents("open"), "incident power.failed ouvert")
            await poll(lambda: len(smtp.messages) >= 1, "mail d'incident", 40)
            assert ADMIN_EMAIL in smtp.rcpts[0]
            st = await pstate()
            assert st and st["observed"] == "on" and st["last_code"]  # échec : observé inchangé
            assert [a[2] for a in await pactions()] == [True, False]

            # --- 5. ADB muet, allumage : WoL envoie le paquet magique (non vérifié, incident reste) ------
            res = await command("on")
            assert res["status"] == "ok" and res["result"]["driver"] == "power.wol", res
            assert res["result"]["verified"] is False
            assert udp.packets and udp.packets[-1][:6] == b"\xff" * 6
            assert len(await incidents("open")) == 1

            async def three_actions() -> Any:
                return len(await pactions()) >= 3

            await poll(three_actions, "action WoL journalisée")
            st = await pstate()
            assert st and (st["observed"], st["driver"], st["verified"]) == (
                "on",
                "power.wol",
                False,
            )

            # --- 6. ADB revient : changement vérifié -> incident résolu + mail de résolution ------------
            adb.mode = "auth"
            res = await command("off")
            assert res["status"] == "ok" and res["result"]["verified"] is True, res
            await poll(lambda: incidents("resolved"), "incident résolu")
            st = await pstate()
            assert st and (st["observed"], st["driver"], st["verified"]) == (
                "off",
                "power.adb",
                True,
            )
            assert await incidents("open") == []
            await poll(lambda: len(smtp.messages) >= 2, "mail de résolution", 40)

            # --- 7. planning poussé (plan.updated) puis exécuté HORS LIGNE (serveur coupé) --------------
            rule = {"weekdays": 127, "start": "08:00", "end": "18:00"}
            rev_before = agent.plan["revision"]
            r = await admin.post("/api/v1/power/plans", json={"name": "Bureau", "rules": [rule]})
            await cfg("site", site, "core", "power.plan_id", r.json()["id"])

            async def with_slots() -> Any:
                e = agent.screen_entry(screen)
                return agent.plan["revision"] != rev_before and e and e["power"]

            await poll(with_slots, "plan avec créneaux reçu")

            async def applied() -> Any:
                return await agent.store.kv_get(f"applied:{screen}")

            before_key = await poll(applied, "créneau courant appliqué")
            slots = await with_slots()
            now = datetime.now(UTC)
            cur = next(
                i for i, s in enumerate(slots)
                if datetime.fromisoformat(s["from"]) <= now < datetime.fromisoformat(s["to"])
            )  # fmt: skip
            assert cur + 1 < len(slots), (now, slots)
            nxt = slots[cur + 1]

            srv.stop()  # coupure serveur
            n_cmd = len(adb.commands)
            shift[0] = datetime.fromisoformat(nxt["from"]) + timedelta(minutes=1) - now

            async def switched() -> Any:
                key = await applied()
                return key != before_key and key

            await poll(switched, "bascule exécutée hors ligne")
            wanted = "KEYCODE_WAKEUP" if nxt["state"] == "on" else "KEYCODE_SLEEP"
            assert any(wanted in c for c in adb.commands[n_cmd:]), adb.commands[n_cmd:]
            assert agent.observed[screen]["observed"] == nxt["state"]

            # --- 8. retour du serveur : reconnexion (backoff) + rejeu de l'outbox -----------------------
            srv.start()
            await srv.wait_ready()

            async def reported() -> Any:
                rows = await srv.sql(
                    "SELECT last_heartbeat FROM site_agent WHERE id = :i", i=agent_id
                )
                hb = rows[0][0] if rows else None
                scr = [x for x in (hb or {}).get("screens", []) if x["screen_id"] == screen]
                return scr and scr[0]["power"]["observed"] == nxt["state"] and scr[0]["power"]

            power = await poll(reported, "état d'alimentation remonté après reconnexion", 60)
            assert power["driver"] == "power.adb"
            # l'outbox est vidée : le serveur a acquitté les événements produits hors ligne

            async def drained() -> bool:
                return (await agent.store.outbox_after(0)) == []

            await poll(drained, "outbox acquittée")

            # observé côté serveur après rejeu de l'outbox (événements produits hors ligne)
            async def replayed() -> Any:
                st = await pstate()
                return st and st["observed"] == nxt["state"] and st

            st = await poll(replayed, "power_state rejoué après reconnexion", 60)
            assert st["driver"] == "power.adb" and st["verified"] is True

            # --- 9. scheduled_off en conditions réelles : le plan résolu fixe desired = off ---------------
            today = datetime.now(UTC).date()
            r = await admin.post(
                "/api/v1/power/exceptions",
                json={
                    "scope_level": "screen",
                    "scope_id": screen,
                    "starts_on": str(today - timedelta(days=1)),
                    "ends_on": str(today + timedelta(days=1)),
                    "state": "off",
                    "label": "Fermeture",
                },
            )
            assert r.status_code == 201, r.text

            async def desired_off() -> Any:
                st = await pstate()
                return st and st["desired"] == "off"

            await poll(desired_off, "power_state desired = off (scheduled_off)")
        finally:
            agent.link.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


@contextlib.asynccontextmanager
async def lan_to_loopback() -> AsyncIterator[None]:
    """La TV factice écoute en boucle locale (refusée par la validation LAN) : on redirige la
    connexion TCP du pilote, sans toucher aux validations serveur et agent."""
    real = asyncio.open_connection

    async def redirected(host: Any = None, port: Any = None, **kw: Any) -> Any:
        return await real("127.0.0.1" if host == TV_LAN else host, port, **kw)

    asyncio.open_connection = redirected  # type: ignore[assignment]
    try:
        yield
    finally:
        asyncio.open_connection = real  # type: ignore[assignment]


async def _provision_setup(stack: dict[str, Any], admin: httpx.AsyncClient) -> dict[str, Any]:
    srv: Server = stack["srv"]
    tmp: Path = stack["tmp"]
    r = await admin.post(
        "/api/v1/m/auth.local/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 200, r.text
    site = (await admin.post("/api/v1/sites", json={"name": "Moulins", "slug": "moulins"})).json()
    screen = (
        await admin.post("/api/v1/screens", json={"site_id": site["id"], "name": "Hall"})
    ).json()
    made = await admin.post(f"/api/v1/sites/{site['id']}/agents", json={"name": "Pi"})
    token = made.json()["token"]
    settings = AgentSettings(
        server_url=srv.url, token=token, data_dir=tmp / "agent",
        modules_dir=ROOT / "modules", ca_file=srv.ca,
    )  # fmt: skip
    agent = Agent(settings, Store(settings.data_dir), heartbeat_s=1.0)
    task = asyncio.create_task(agent.run())
    caps_url = f"/api/v1/provisioning/capabilities?site_id={site['id']}"

    async def ready() -> bool:
        r = await admin.get(caps_url)
        return r.status_code == 200 and "android" in json.dumps(r.json())

    await poll(ready, "agent connecté avec provision.android", 40)
    return {"site": site["id"], "screen": screen["id"], "agent": agent, "task": task}


async def _stop(ctx: dict[str, Any]) -> None:
    ctx["agent"].link.stop()
    ctx["task"].cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await ctx["task"]


async def test_provision_android_serveur_agent_adb_player(stack: dict[str, Any]) -> None:
    """Parcours complet : API serveur -> commande agent -> power.adb (faux ADB) -> player appairé."""
    srv: Server = stack["srv"]
    fakes_adb = load_fakes("power.adb")
    async with fakes_adb.FakeAdbTv(mode="auth") as adb, srv.client() as admin, lan_to_loopback():
        ctx = await _provision_setup(stack, admin)
        try:
            r = await admin.post(
                f"/api/v1/screens/{ctx['screen']}/provision",
                json={"platform": "android", "address": TV_LAN, "port": adb.port,
                      "device_owner": "try"},
            )  # fmt: skip
            assert r.status_code == 201, r.text
            pid = r.json()["id"]
            assert "pairing_ticket" not in r.text

            async def launched() -> Any:
                return next((c for c in adb.commands if "pairing_ticket" in c), None)

            cmd = await poll(launched, "player lancé avec le ticket (am start)", 60)
            ticket = cmd.split("--es pairing_ticket ")[1].strip("' \x00")
            assert bytes(adb.received_apk) == APK_BYTES and adb.version_code == 7
            assert any(c.startswith("shell:dpm set-device-owner") for c in adb.commands)
            assert len(adb.accepted) == 1  # clé RSA de l'agent mémorisée par la TV

            async with srv.client() as player:
                r = await player.post(
                    "/api/v1/player/pair",
                    json={"pairing_ticket": ticket,
                          "device": {"platform": "android", "model": "TCL", "app_version": "1.0.0",
                                     "screen_w": 1920}},
                )  # fmt: skip
                assert r.status_code == 200, r.text
                dev = r.json()["device_token"]
                assert r.json()["screen_id"] == ctx["screen"]
                again = await player.post(
                    "/api/v1/player/pair",
                    json={"pairing_ticket": ticket, "device": {"platform": "android"}},
                )
                assert again.status_code == 401  # ticket à usage unique
                hb = await player.post(
                    "/api/v1/player/heartbeat", json={}, headers={"Authorization": f"Bearer {dev}"}
                )
                assert hb.status_code == 200, hb.text

            async def done() -> Any:
                got = (await admin.get(f"/api/v1/provisioning/{pid}")).json()
                return got if got["status"] == "done" else None

            got = await poll(done, "session validée (premier heartbeat)", 30)
            assert got["outcome"]["kiosk"] == "device_owner"
            assert "pairing_ticket" not in json.dumps(got) and ticket not in json.dumps(got)
            # le pilote d'alimentation fonctionne toujours après le provisioning
            assert any("KEYCODE" not in c for c in adb.commands)
        finally:
            await _stop(ctx)


async def test_provision_android_invite_rsa_refusee(stack: dict[str, Any]) -> None:
    """Invite de débogage refusée sur la TV : échec lisible (code), ticket jamais exposé."""
    srv: Server = stack["srv"]
    fakes_adb = load_fakes("power.adb")
    async with fakes_adb.FakeAdbTv(mode="deny") as adb, srv.client() as admin, lan_to_loopback():
        ctx = await _provision_setup(stack, admin)
        try:
            r = await admin.post(
                f"/api/v1/screens/{ctx['screen']}/provision",
                json={"platform": "android", "address": TV_LAN, "port": adb.port},
            )
            pid = r.json()["id"]

            async def failed() -> Any:
                got = (await admin.get(f"/api/v1/provisioning/{pid}")).json()
                return got if got["status"] == "failed" else None

            got = await poll(failed, "échec de l'autorisation ADB", 60)
            assert got["code"] and got["code"].startswith("provision.")
            assert not adb.received_apk and not any("pairing_ticket" in c for c in adb.commands)
            assert "pairing_ticket" not in json.dumps(got)
        finally:
            await _stop(ctx)
