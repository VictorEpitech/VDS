import json
import sys
from pathlib import Path

import pytest
from vds_agent.testing import MemoryState, load_driver_class, load_fakes, make_ctx
from vds_sdk import load_manifest
from vds_sdk.agent import DetectHint, PowerTarget

fakes = load_fakes("power.adb")
Driver = load_driver_class("power.adb")
SID = "11111111-1111-1111-1111-111111111111"


def target(port: int, **cfg: object) -> PowerTarget:
    return PowerTarget(
        screen_id=SID,
        site_timezone="Europe/Paris",
        address="127.0.0.1",  # type: ignore[arg-type]
        config={"port": port, "timeout_ms": 1500, **cfg},
    )


async def make_driver(state: MemoryState | None = None):  # type: ignore[no-untyped-def]
    d = Driver()
    await d.setup(make_ctx("power.adb", state))
    return d


def test_manifest_valid() -> None:
    m = load_manifest(Path(__file__).parents[1] / "module.json")
    assert m.kind == "power-driver"


async def test_pairing_then_sleep_and_state() -> None:
    state = MemoryState()
    async with fakes.FakeAdbTv("auth") as tv:
        d = await make_driver(state)
        t = target(tv.port)
        r = await d.set_power(t, "off")
        assert r.ok and tv.commands == ["shell:input keyevent KEYCODE_SLEEP"]
        assert await d.get_state(t) == "standby"
        assert (await d.set_power(t, "on")).ok
        assert await d.get_state(t) == "on"
        assert len(tv.accepted) == 1  # clé enregistrée une seule fois
    assert "BEGIN PRIVATE KEY" in state.data["adb_private_key"]


async def test_big_output_chunks() -> None:
    async with fakes.FakeAdbTv("open") as tv:
        d = await make_driver()
        assert await d.get_state(target(tv.port)) == "on"


async def test_denied_is_unauthorized() -> None:
    async with fakes.FakeAdbTv("deny") as tv:
        d = await make_driver()
        r = await d.set_power(target(tv.port), "on")
        assert not r.ok and r.code == "power.adb.unauthorized"


async def test_timeout_and_unreachable() -> None:
    async with fakes.FakeAdbTv("hang") as tv:
        d = await make_driver()
        r = await d.set_power(target(tv.port, timeout_ms=500), "on")
        assert r.code == "power.adb.timeout"
        assert await d.get_state(target(tv.port, timeout_ms=500)) == "unknown"
        assert (
            await d.get_state(target(tv.port, timeout_ms=500, unreachable_means_off=True)) == "off"
        )
    d = await make_driver()
    assert (await d.set_power(target(1), "on")).code == "power.adb.unreachable"


async def test_stls_not_supported() -> None:
    async with fakes.FakeAdbTv("stls") as tv:
        d = await make_driver()
        assert (await d.set_power(target(tv.port), "on")).code == "power.adb.tls_required"


async def test_detect_without_address() -> None:
    d = await make_driver()
    assert await d.detect(DetectHint()) is None


def test_schema_json_loads() -> None:
    json.loads((Path(__file__).parents[1] / "config.schema.json").read_text())


def test_host_format_validated() -> None:
    """M5 : IP ou nom d'hôte uniquement (pas d'URL, de chemin, d'espace ni d'arobase)."""
    import jsonschema

    for name in ("power.adb", "power.samsung-ws"):
        schema = json.loads((Path(__file__).parents[2] / name / "config.schema.json").read_text())
        for ok in ("", "192.168.1.20", "tv-salle1.lan", "fe80::1"):
            jsonschema.validate({"host": ok}, schema)
        for bad in ("evil.tld/#", "a b", "http://x", "x@y", "x:8002", "x?y", "-x", "x\n"):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate({"host": bad}, schema)


def test_mac_format_validated() -> None:
    """MAC : six octets hexa séparés par « : » ou « - », ou vide ; pas de saut de ligne final."""
    import jsonschema

    for name in ("power.wol", "power.samsung-ws"):
        schema = json.loads((Path(__file__).parents[2] / name / "config.schema.json").read_text())
        for ok in ("", "AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff"):
            jsonschema.validate({"mac": ok}, schema)
        for bad in ("aabbccddeeff", "aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:gg", "aa:bb:cc:dd:ee:ff\n"):
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate({"mac": bad}, schema)


# --- provisioning Android (capacité provision.android) ---

import asyncio  # noqa: E402
import hashlib  # noqa: E402
from ipaddress import IPv4Address  # noqa: E402
from uuid import uuid4  # noqa: E402

from vds_sdk.agent import ApkRef, ProvisionRequest, StepReport  # noqa: E402

APK = b"fake-apk-" * 5000
TICKET = "T" * 32


class FakeApkSource:
    def __init__(self, path: Path, ok: bool = True) -> None:
        self.path, self.ok, self.calls = path, ok, 0

    async def fetch(self, apk: ApkRef) -> Path:
        self.calls += 1
        if not self.ok:
            raise RuntimeError("down")
        return self.path


def prov_req(port: int, **over: object) -> ProvisionRequest:
    raw: dict[str, object] = {
        "provision_id": str(uuid4()),
        "screen_id": SID,
        "platform": "android",
        "address": "192.168.1.20",
        "port": port,
        "apk": {
            "package": "app.vds.player",
            "version_code": 7,
            "version_name": "1.0.7",
            "sha256": hashlib.sha256(APK).hexdigest(),
            "size": len(APK),
        },
        "server_url": "https://vds.example/espace/affichage",
        "pairing_ticket": TICKET,
    }
    raw.update(over)
    # le fake écoute en boucle locale, refusée par la validation LAN : on la pose après coup
    req = ProvisionRequest.model_validate(raw)
    return req.model_copy(update={"address": IPv4Address("127.0.0.1")})


def apk_file(tmp_path: Path) -> Path:
    p = tmp_path / "p.apk"
    p.write_bytes(APK)
    return p


async def run_prov(
    tv,
    tmp_path: Path,
    on_report=None,
    source_ok: bool = True,
    state=None,
    **over: object,  # type: ignore[no-untyped-def]
):
    d = await make_driver(state)
    reports: list[StepReport] = []

    async def report(r: StepReport) -> None:
        reports.append(r)
        if on_report:
            await on_report(r)

    src = FakeApkSource(apk_file(tmp_path), source_ok)
    out = await d.provision(prov_req(tv.port, **over), src, report)
    return out, reports, src


def status(reports: list[StepReport], step: str) -> StepReport:
    return [r for r in reports if r.step == step][-1]


async def test_manifest_declares_provision() -> None:
    m = load_manifest(Path(__file__).parents[1] / "module.json")
    assert "provision.android" in m.capabilities and "screens.provision" in m.permissions
    assert Driver.platforms == frozenset({"android"})


async def test_provision_device_owner_happy_path(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("auth") as tv:
        out, reports, _ = await run_prov(tv, tmp_path)
        assert out.ok and out.kiosk == "device_owner" and out.installed_version_code == 7
        assert bytes(tv.received_apk) == APK  # flux complet, sans copie en mémoire côté agent
        assert status(reports, "install").status == "ok"
        assert status(reports, "device_owner").detail == {}
        assert status(reports, "soft_kiosk").status == "skipped"
        # ADB réseau reste allumé en Device Owner : `launch` s'exécute (décision assumée)
        assert tv.commands[-1].startswith("shell:am start -n app.vds.player/.MainActivity")
        assert status(reports, "launch").status == "ok"
        assert out.code is None
        inject = [c for c in tv.commands if "ProvisionActivity" in c]
        assert len(inject) == 1 and TICKET in inject[0]
        assert "--es server_url https://vds.example/espace/affichage" in inject[0]
        assert TICKET not in "".join(r.model_dump_json() for r in reports)


async def test_provision_rsa_prompt_waits_then_accepted(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("prompt") as tv:

        async def on_report(r: StepReport) -> None:
            if r.status == "waiting_user":
                assert r.step == "authorize" and r.code == "provision.android.accept_on_tv"
                tv.accept_event.set()

        out, reports, _ = await run_prov(tv, tmp_path, on_report)
        assert out.ok
        seq = [(r.step, r.status) for r in reports[:4]]
        assert seq == [
            ("connect", "running"),
            ("connect", "ok"),
            ("authorize", "waiting_user"),
            ("authorize", "ok"),
        ]


async def test_provision_rsa_prompt_never_accepted(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import sys

    mod = next(m for n, m in sys.modules.items() if n.endswith("provision_android"))
    monkeypatch.setattr(mod, "AUTHORIZE_TIMEOUT", 0.4)
    async with fakes.FakeAdbTv("prompt") as tv:
        out, reports, _ = await run_prov(tv, tmp_path)
        assert not out.ok and out.code == "provision.android.unauthorized"
        assert status(reports, "authorize").status == "failed"
        tv.accept_event.set()


async def test_provision_tls_required(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("stls") as tv:
        out, reports, _ = await run_prov(tv, tmp_path)
        assert out.code == "provision.android.tls_required"
        assert status(reports, "connect").status == "failed"


async def test_provision_unreachable(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        port = tv.port
    out, reports, _ = await run_prov(tv, tmp_path)
    assert port and out.code == "provision.android.unreachable"
    assert status(reports, "connect").status == "failed"


async def test_provision_accounts_present_soft_kiosk(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.accounts = 2
        out, reports, _ = await run_prov(tv, tmp_path)
        do = status(reports, "device_owner")
        assert (do.status, do.code, do.detail) == (
            "skipped",
            "provision.android.accounts_present",
            {"accounts": 2},
        )
        assert not any("set-device-owner" in c for c in tv.commands)
        sk = status(reports, "soft_kiosk")
        assert sk.status == "ok" and sk.code == "provision.android.soft_kiosk_active"
        assert out.ok and out.kiosk == "soft"
        assert out.code is None
        assert status(reports, "launch").status == "ok"


async def test_provision_no_soft_fallback_means_no_kiosk(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        out, _, _ = await run_prov(tv, tmp_path, device_owner="skip", soft_kiosk_fallback=False)
        assert out.ok and out.kiosk == "none" and out.code == "provision.android.no_kiosk"


async def test_provision_incompatible_signature(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.install_output = (
            "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match]\n"
        )
        out, reports, _ = await run_prov(tv, tmp_path)
        assert not out.ok and out.code == "provision.android.install_incompatible"
        assert status(reports, "install").status == "failed"
        assert not any("ProvisionActivity" in c for c in tv.commands)


async def test_provision_already_installed_skips_install(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.version_code = 7
        out, reports, src = await run_prov(tv, tmp_path)
        assert out.ok and src.calls == 0 and not tv.received_apk
        assert status(reports, "install").status == "skipped"
        assert not any(c.startswith("exec:") for c in tv.commands)


async def test_provision_apk_unavailable_and_mismatch(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        out, _, _ = await run_prov(tv, tmp_path, source_ok=False)
        assert out.code == "provision.android.apk_unavailable"
        bad = prov_req(tv.port).apk.model_copy(update={"size": len(APK) + 1})
        out, _, _ = await run_prov(tv, tmp_path, apk=bad.model_dump())
        assert out.code == "provision.android.apk_mismatch"
        assert not (tmp_path / "p.apk").exists()


async def test_provision_sdk_too_old(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.sdk = "21"
        out, _, _ = await run_prov(tv, tmp_path)
        assert out.code == "provision.android.sdk_too_old"


async def test_provision_resume_from_device_owner(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.version_code = 7
        out, reports, src = await run_prov(tv, tmp_path, from_step="device_owner")
        assert out.ok and out.kiosk == "device_owner" and src.calls == 0
        assert {r.step for r in reports} >= {"inspect", "device_owner", "launch"}
        assert "download" not in {r.step for r in reports}


async def test_provision_power_driver_still_works_after(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("auth") as tv:
        tv.accounts = 1  # kiosque doux
        state = MemoryState()
        await run_prov(tv, tmp_path, state=state)
        d = await make_driver(state)
        assert await d.get_state(target(tv.port)) == "on"
        assert len(tv.accepted) == 1
        await asyncio.sleep(0)


async def test_shell_output_is_bounded() -> None:
    async with fakes.FakeAdbTv("auth") as tv:
        d = await make_driver()
        with pytest.raises(Exception) as exc:  # noqa: PT011 - AdbError chargé dynamiquement
            await d._transport.shell("127.0.0.1", tv.port, "flood", 5.0)  # M3
        assert getattr(exc.value, "code", "") == "power.adb.protocol"


# --- mise à jour Android (capacité update.android) ---

from vds_sdk.agent import ApkUpdateRequest  # noqa: E402


def upd_req(**over: object) -> ApkUpdateRequest:
    raw: dict[str, object] = {
        "request_id": str(uuid4()),
        "screen_id": SID,
        "platform": "android",
        "apk": {
            "package": "app.vds.player",
            "version_code": 7,
            "version_name": "1.0.7",
            "sha256": hashlib.sha256(APK).hexdigest(),
            "size": len(APK),
        },
    }
    raw.update(over)
    return ApkUpdateRequest.model_validate(raw)


async def run_upd(
    tv, tmp_path: Path, monkeypatch, host: str | None = "127.0.0.1", prime: bool = True, **over
):  # type: ignore[no-untyped-def]
    mod = next(m for n, m in sys.modules.items() if n.endswith("update_android"))
    monkeypatch.setattr(mod, "ensure_lan_address", lambda ip: ip)  # le fake écoute en boucle locale
    d = await make_driver()
    if prime:  # clé déjà autorisée sur la TV (cas normal d'un écran provisionné)
        await d.get_state(target(tv.port))
        tv.commands.clear()
    cfg: dict[str, object] = {"port": tv.port}
    if host:
        cfg["host"] = host
    d._ctx.screen_configs[SID] = cfg
    reports: list[StepReport] = []

    async def report(r: StepReport) -> None:
        reports.append(r)

    src = FakeApkSource(apk_file(tmp_path))
    out = await d.update(upd_req(**over), src, report)
    return out, reports, src


async def test_manifest_declares_update() -> None:
    m = load_manifest(Path(__file__).parents[1] / "module.json")
    assert "update.android" in m.capabilities and m.version == "0.4.0"


async def test_update_happy_path(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 5
        out, reports, src = await run_upd(tv, tmp_path, monkeypatch)
    assert out.ok and out.previous_version_code == 5 and out.installed_version_code == 7
    assert src.calls == 1 and bytes(tv.received_apk) == APK
    assert any("install -r" in c for c in tv.commands) and any(
        c.startswith("shell:am start") for c in tv.commands
    )
    assert not any("uninstall" in c or "set-device-owner" in c for c in tv.commands)
    assert status(reports, "launch").status == "ok"


async def test_update_already_up_to_date_skips_install(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 7
        out, _, src = await run_upd(tv, tmp_path, monkeypatch)
    assert out.ok and out.code == "update.android.already_up_to_date"
    assert src.calls == 0 and not tv.received_apk


async def test_update_downgrade_refused(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 9
        out, _, src = await run_upd(tv, tmp_path, monkeypatch)
    assert not out.ok and out.code == "update.android.downgrade_refused"
    assert src.calls == 0 and not tv.received_apk


async def test_update_incompatible_signature_no_uninstall(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 5
        tv.install_output = "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: x]\n"
        out, _, _ = await run_upd(tv, tmp_path, monkeypatch)
    assert out.code == "update.android.install_incompatible" and not out.ok
    assert not any("uninstall" in c for c in tv.commands)


async def test_update_install_failed_and_version_unchanged(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 5
        tv.install_output = "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]\n"
        out, _, _ = await run_upd(tv, tmp_path, monkeypatch)
    assert out.code == "update.android.install_failed"


async def test_update_unauthorized_fails_fast(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("prompt") as tv:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        out, reports, src = await run_upd(tv, tmp_path, monkeypatch, prime=False)
        tv.accept_event.set()  # libère le fake (sinon sa fermeture attend l'utilisateur)
    assert out.code == "update.android.adb_unauthorized" and loop.time() - t0 < 3
    assert src.calls == 0 and status(reports, "connect").status == "failed"


async def test_update_unreachable(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        port = tv.port
    tv.port = port
    out, _, _ = await run_upd(tv, tmp_path, monkeypatch)
    assert out.code == "update.android.adb_unreachable"


async def test_update_apk_unavailable_and_mismatch(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 5
        d_out, _, _ = await run_upd(
            tv,
            tmp_path,
            monkeypatch,
            apk={
                "package": "app.vds.player",
                "version_code": 7,
                "version_name": "x",
                "sha256": "0" * 64,
                "size": len(APK),
            },
        )  # fichier local != sha annoncé
    assert d_out.code == "update.android.verify_failed"


async def test_update_no_host_never_connects(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        out, _, _ = await run_upd(tv, tmp_path, monkeypatch, host=None)
        assert out.code == "update.android.no_adb_host" and tv.commands == []


async def test_update_refuses_public_address(tmp_path: Path) -> None:
    d = await make_driver()
    d._ctx.screen_configs[SID] = {"host": "8.8.8.8"}
    out = await d.update(upd_req(), FakeApkSource(apk_file(tmp_path)), lambda r: asyncio.sleep(0))
    assert out.code == "update.android.address_not_lan"


# --- relance de l'application (capacité app.restart.android) ---

from vds_sdk.agent import AppRestartRequest  # noqa: E402


async def run_rst(tv, monkeypatch, host: str | None = "127.0.0.1", prime: bool = True):  # type: ignore[no-untyped-def]
    mod = next(m for n, m in sys.modules.items() if n.endswith("restart_android"))
    monkeypatch.setattr(mod, "ensure_lan_address", lambda ip: ip)
    d = await make_driver()
    if prime:
        await d.get_state(target(tv.port))
        tv.commands.clear()
    cfg: dict[str, object] = {"port": tv.port}
    if host:
        cfg["host"] = host
    d._ctx.screen_configs[SID] = cfg
    req = AppRestartRequest(request_id=uuid4(), screen_id=SID, platform="android")
    return await d.restart_app(req)


async def test_restart_manifest_declares_capability() -> None:
    m = load_manifest(Path(__file__).parents[1] / "module.json")
    assert "app.restart.android" in m.capabilities and m.version == "0.4.0"
    assert m.core == ">=1.14 <2.0"


async def test_restart_exact_command_sequence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        tv.version_code = 7
        out = await run_rst(tv, monkeypatch)
    assert out.ok and out.code is None
    assert [c for c in tv.commands if not c.startswith("shell:getprop")] == [
        "shell:pm list packages --show-versioncode app.vds.player",
        "shell:am force-stop app.vds.player",
        "shell:am start -n app.vds.player/.MainActivity",
    ]


async def test_restart_not_installed(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        out = await run_rst(tv, monkeypatch)
    assert out.code == "app.restart.not_installed"
    assert not any("force-stop" in c or "am start" in c for c in tv.commands)


async def test_restart_unauthorized_fails_fast_without_command(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("prompt") as tv:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        out = await run_rst(tv, monkeypatch, prime=False)
        tv.accept_event.set()  # libère le fake (sinon sa fermeture attend l'utilisateur)
    assert out.code == "app.restart.adb_unauthorized" and loop.time() - t0 < 3
    assert tv.commands == []


async def test_restart_unreachable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        pass  # serveur fermé en sortie : le port ne répond plus
    out = await run_rst(tv, monkeypatch, prime=False)
    assert out.code == "app.restart.adb_unreachable"


async def test_restart_no_host_never_connects(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async with fakes.FakeAdbTv("auth") as tv:
        out = await run_rst(tv, monkeypatch, host=None)
        assert out.code == "app.restart.adb_unreachable" and tv.commands == []


async def test_restart_refuses_public_address() -> None:
    d = await make_driver()
    d._ctx.screen_configs[SID] = {"host": "8.8.8.8"}
    req = AppRestartRequest(request_id=uuid4(), screen_id=SID, platform="android")
    assert (await d.restart_app(req)).code == "app.restart.adb_unreachable"


class _FlakyLaunch:
    """Transport factice (aucun réseau) : `am start` échoue `fails` fois."""

    def __init__(self, fails: int, exc: Exception | None = None) -> None:
        self.fails, self.exc, self.launches = fails, exc, 0

    async def probe(self, *a, **k) -> str:  # type: ignore[no-untyped-def]
        return "ok"

    async def shell(self, host, port, command, timeout) -> str:  # type: ignore[no-untyped-def]
        if "list packages" in command:
            return "package:app.vds.player versionCode:7"
        if "am start" in command:
            self.launches += 1
            if self.launches <= self.fails:
                if self.exc:
                    raise self.exc
                return "Error: Activity class does not exist"
        return ""


async def _restart_flaky(t, monkeypatch):  # type: ignore[no-untyped-def]
    mod = next(m for n, m in sys.modules.items() if n.endswith("restart_android"))
    monkeypatch.setattr(mod, "ensure_lan_address", lambda ip: ip)
    return await mod.AndroidAppRestarter(t).restart("127.0.0.1", 5555)


async def test_restart_retries_am_start_once(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mod = next(m for n, m in sys.modules.items() if n.endswith("restart_android"))
    for exc in (None, TimeoutError(), mod.AdbError("power.adb.protocol")):
        t = _FlakyLaunch(1, exc)
        assert (await _restart_flaky(t, monkeypatch)).ok and t.launches == 2
    t = _FlakyLaunch(2)
    assert (await _restart_flaky(t, monkeypatch)).code == "app.restart.launch_failed"
    assert t.launches == 2  # une seule nouvelle tentative
