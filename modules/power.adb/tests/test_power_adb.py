import json
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
        assert status(reports, "device_owner").detail == {"debugging_restricted": True}
        assert status(reports, "soft_kiosk").status == "skipped"
        # I2 : adbd est coupé par l'app après `inject`, aucune connexion ADB ne suit
        assert tv.adb_cut and tv.commands[-1].startswith("shell:am start -n app.vds.player/.Prov")
        assert status(reports, "launch").status == "skipped"
        assert status(reports, "launch").code == "provision.android.launch_by_app"
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
        # I3 : avertissement explicite, ADB reste ouvert, étape launch exécutée
        assert out.code == "provision.android.adb_open"
        assert sk.detail == {"adb_open": True, "adb_port": tv.port}
        assert status(reports, "launch").status == "ok" and not tv.adb_cut


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
        tv.accounts = 1  # kiosque doux : ADB reste ouvert (en Device Owner il est coupé)
        state = MemoryState()
        await run_prov(tv, tmp_path, state=state)
        d = await make_driver(state)
        assert await d.get_state(target(tv.port)) == "on"
        assert len(tv.accepted) == 1
        await asyncio.sleep(0)


async def test_disable_network_debugging_after_soft_kiosk(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.accounts = 1
        await run_prov(tv, tmp_path)
        d = await make_driver()
        assert not tv.adb_cut
        assert await d.disable_network_debugging(prov_req(tv.port))
        assert tv.adb_cut
        # TV injoignable ensuite : retour False, sans exception
        assert not await d.disable_network_debugging(prov_req(tv.port))


async def test_final_step_disables_adb_only_with_ack(tmp_path: Path) -> None:
    async with fakes.FakeAdbTv("open") as tv:
        tv.accounts = 2
        out, _, _ = await run_prov(tv, tmp_path)
        assert out.kiosk == "soft" and out.code == "provision.android.adb_open"
    async with fakes.FakeAdbTv("open") as tv:
        tv.accounts = 2
        out, _, _ = await run_prov(tv, tmp_path, non_public_ack=True)
        assert out.ok and out.code == "provision.android.adb_closed"
