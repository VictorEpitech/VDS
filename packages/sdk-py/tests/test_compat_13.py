"""Contrat vds-core 1.3.0 : provisioning d'écran (additif sur 1.2)."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_2,
    CORE_EVENT_PAYLOAD_1_3,
    CORE_EVENTS_1_2,
    CORE_EVENTS_1_3,
    ContractError,
    load_manifest,
    parse_event,
    permissions,
)
from vds_sdk.agent import ProvisionRequest, ScreenProvisioner, check_provisioner
from vds_sdk.manifest import ModuleManifest
from vds_sdk.wire import messages
from vds_sdk.wire.messages import Cmd, ScreenProvisionArgs

SHA = "a" * 64
TICKET = "Zm9vYmFyYmF6cXV4MTIzNDU2"


def apk() -> dict[str, Any]:
    return {
        "package": "app.vds.player",
        "version_code": 3,
        "version_name": "1.0.3",
        "sha256": SHA,
        "size": 1000,
    }


def args(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "provision_id": str(uuid4()),
        "platform": "android",
        "address": "192.168.1.20",
        "apk": apk(),
        "server_url": "https://vds.example.org/espace/affichage",
        "pairing_ticket": TICKET,
    }
    raw.update(over)
    return raw


def manifest(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "power.adb",
        "version": "0.2.0",
        "core": ">=1.3 <2.0",
        "kind": "power-driver",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["site-agent"],
        "entrypoints": {"site-agent": "m:D"},
        "capabilities": ["power.on", "provision.android"],
        "permissions": ["lan.access", "power.control", "screens.provision"],
        "author": "V-Tech",
    }
    raw.update(over)
    return raw


def test_version_and_permission() -> None:
    assert CORE_API_VERSION == "1.3.0"
    assert "screens.provision" in permissions.PERMISSIONS


def test_events_1_3_additive() -> None:
    new = {
        "screen.provision_started",
        "screen.provision_progressed",
        "screen.provisioned",
        "screen.provision_failed",
    }
    assert CORE_EVENTS_1_2 < CORE_EVENTS_1_3
    assert new == CORE_EVENTS_1_3 - CORE_EVENTS_1_2
    assert CORE_EVENT_PAYLOAD_1_2.items() <= CORE_EVENT_PAYLOAD_1_3.items()
    for name in CORE_EVENTS_1_3:
        parse_event(name)


def test_provision_manifest_valid_and_loads(tmp_path: Path) -> None:
    ModuleManifest.model_validate(manifest())
    (tmp_path / "module.json").write_text(json.dumps(manifest()))
    assert load_manifest(tmp_path / "module.json").id == "power.adb"


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"core": ">=1.2 <2.0"}, "contract.provision_requires_1_3"),
        ({"permissions": ["lan.access"]}, "module.provision_permission_missing"),
        ({"capabilities": ["provision.Android"]}, "module.provision_capability_invalid"),
        ({"capabilities": ["power.on", "bogus"]}, "module.power_driver_capability_invalid"),
    ],
)
def test_provision_manifest_rejected(over: dict[str, Any], code: str) -> None:
    with pytest.raises(ValidationError, match=code):
        ModuleManifest.model_validate(manifest(**over))


def test_modules_1_0_to_1_2_load_unchanged(tmp_path: Path) -> None:
    old = manifest(
        core=">=1.2 <2.0", capabilities=["power.on"], permissions=["lan.access", "power.control"]
    )
    (tmp_path / "module.json").write_text(json.dumps(old))
    assert load_manifest(tmp_path / "module.json", core_version="1.2.0").id == "power.adb"
    assert load_manifest(tmp_path / "module.json", core_version="1.3.0").id == "power.adb"
    (tmp_path / "module.json").write_text(json.dumps(manifest()))
    with pytest.raises(ContractError):
        load_manifest(tmp_path / "module.json", core_version="1.2.0")


def test_check_provisioner_at_load() -> None:
    class Ok:
        platforms = frozenset({"android"})

        async def provision(self, req: Any, apk: Any, report: Any) -> Any: ...

    class Wrong:
        platforms = frozenset({"tizen"})

        async def provision(self, req: Any, apk: Any, report: Any) -> Any: ...

    assert isinstance(Ok(), ScreenProvisioner)
    check_provisioner(Ok(), ["provision.android"])
    check_provisioner(object(), ["power.on"])  # pas de capacité provision : rien à vérifier
    with pytest.raises(ContractError, match="module.provisioner_missing"):
        check_provisioner(object(), ["provision.android"])
    with pytest.raises(ContractError, match="module.provisioner_platform_mismatch"):
        check_provisioner(Wrong(), ["provision.android"])


def test_request_secret_and_lan_only() -> None:
    sid = str(uuid4())
    req = ProvisionRequest.model_validate(args(screen_id=sid))
    assert TICKET not in repr(req) and TICKET not in req.model_dump_json()
    for bad in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "224.0.0.1", "2001:4860:4860::8888"):  # noqa: S104
        with pytest.raises(ValidationError):
            ProvisionRequest.model_validate(args(screen_id=sid, address=bad))
    ProvisionRequest.model_validate(args(screen_id=sid, address="fd00::5"))
    ProvisionRequest.model_validate(args(screen_id=sid, address="169.254.1.2"))


def test_apk_ref_is_not_a_url_and_bounded() -> None:
    for bad in (
        {"sha256": "A" * 64},
        {"size": 201 * 1024 * 1024},
        {"version_code": 0},
        {"package": "evil.app"},
    ):
        with pytest.raises(ValidationError):
            ScreenProvisionArgs.model_validate(args(apk={**apk(), **bad}))


def test_wire_command_strict() -> None:
    assert "screen.provision" in messages.AGENT_COMMANDS
    assert "screen.provision" not in messages.PLAYER_COMMANDS
    ok = ScreenProvisionArgs.model_validate(args())
    assert TICKET not in repr(ok)
    for over in (
        {"address": "8.8.8.8"},
        {"address": "pas-une-ip"},
        {"pairing_ticket": "a b;rm -rf"},
        {"pairing_ticket": "x" * 65},
        {"extra": 1},
        {"port": 0},
        {"from_step": "format_disk"},
    ):
        with pytest.raises(ValidationError):
            ScreenProvisionArgs.model_validate(args(**over))


def test_agent_1_2_rejects_screen_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simule un agent 1.2 : table de commandes sans `screen.provision` => unknown_command."""
    cmd = Cmd.model_validate(
        {
            "cmd_id": str(uuid4()),
            "name": "screen.provision",
            "screen_id": str(uuid4()),
            "args": args(),
            "expires_at": "2026-10-26T12:00:00Z",
        }
    )
    assert isinstance(cmd.validate_for("agent"), ScreenProvisionArgs)
    old = {k: v for k, v in messages.AGENT_COMMAND_ARGS.items() if k in messages.AGENT_COMMANDS_1_2}
    monkeypatch.setattr(messages, "AGENT_COMMAND_ARGS", old)
    with pytest.raises(ValueError, match="unknown_command"):
        cmd.validate_for("agent")
