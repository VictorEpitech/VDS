"""Contrat vds-core 1.5.0 : découverte LAN (additif sur 1.4)."""

import json
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

import pytest
from pydantic import ValidationError
from vds_sdk import (
    CORE_API_VERSION,
    CORE_EVENT_PAYLOAD_1_4,
    CORE_EVENT_PAYLOAD_1_5,
    CORE_EVENTS_1_4,
    CORE_EVENTS_1_5,
    ConfigSchemaError,
    ContractError,
    load_manifest,
    parse_event,
    validate_config_schema,
)
from vds_sdk.agent import DISCOVERY_PORTS, DiscoveredDevice, DiscoveryScopeError, check_discoverer
from vds_sdk.agent.discovery import ensure_discovery_ports
from vds_sdk.events import CRITICAL_EVENTS_1_4, CRITICAL_EVENTS_1_5
from vds_sdk.permissions import PERMISSIONS
from vds_sdk.wire import (
    AGENT_COMMAND_ARGS,
    DiscoveryBatch,
    DiscoveryResult,
    DiscoveryScanArgs,
)
from vds_sdk.wire.messages import Cmd


def discovery(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "discovery.lan",
        "version": "0.1.0",
        "core": ">=1.5 <2.0",
        "kind": "discovery",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["site-agent"],
        "entrypoints": {"site-agent": "m:D"},
        "capabilities": ["discovery.lan"],
        "permissions": ["network.discover"],
        "author": "V-Tech",
    }
    raw.update(over)
    return raw


def load(tmp_path: Path, raw: dict[str, Any], core: str = "1.5.0") -> Any:
    (tmp_path / "module.json").write_text(json.dumps(raw))
    return load_manifest(tmp_path / "module.json", core_version=core)


def test_version_and_permission() -> None:
    assert CORE_API_VERSION in {"1.5.0", "1.6.0", "1.7.0", "1.8.0", "1.9.0"}
    assert "network.discover" in PERMISSIONS


def test_events_1_5_additive() -> None:
    new = {"discovery.started", "discovery.completed", "discovery.failed"}
    assert new == CORE_EVENTS_1_5 - CORE_EVENTS_1_4
    assert CORE_EVENT_PAYLOAD_1_4.items() <= CORE_EVENT_PAYLOAD_1_5.items()
    assert CRITICAL_EVENTS_1_5 == CRITICAL_EVENTS_1_4
    for name in new:
        parse_event(name + "@1")
        keys = set(CORE_EVENT_PAYLOAD_1_5[name])
        assert not keys & {"ip", "mac", "name", "devices"}


def test_discovery_module_loads(tmp_path: Path) -> None:
    assert load(tmp_path, discovery()).kind == "discovery"


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"permissions": []}, "module.discovery_permission_missing"),
        ({"capabilities": ["discovery.wan"]}, "module.discovery_capability_invalid"),
        ({"capabilities": []}, "module.discovery_capability_invalid"),
        ({"core": ">=1.4 <2.0"}, "contract.discovery_requires_1_5"),
        (
            {"targets": ["server"], "entrypoints": {"server": "m:D"}},
            "module.discovery_target_invalid",
        ),
        (
            {
                "targets": ["site-agent", "server"],
                "entrypoints": {"site-agent": "m:D", "server": "m:D"},
            },
            "module.discovery_target_invalid",
        ),
    ],
)
def test_discovery_manifest_refused(tmp_path: Path, over: dict[str, Any], code: str) -> None:
    with pytest.raises(ValidationError, match=code):
        load(tmp_path, discovery(**over))


def test_network_discover_on_power_driver_refused(tmp_path: Path) -> None:
    raw = discovery(
        id="power.adb",
        kind="power-driver",
        capabilities=["power.on"],
        permissions=["lan.access", "power.control", "network.discover"],
    )
    with pytest.raises(ValidationError, match="module.permission_kind_forbidden"):
        load(tmp_path, raw)


def test_discovery_capability_on_other_kind_refused(tmp_path: Path) -> None:
    raw = discovery(id="storage.x", kind="storage", permissions=[])
    with pytest.raises(ValidationError, match="module.discovery_capability_forbidden"):
        load(tmp_path, raw)


def test_power_driver_with_core_1_5_loads(tmp_path: Path) -> None:
    raw = discovery(
        id="power.adb",
        kind="power-driver",
        core=">=1.5 <2.0",
        capabilities=["power.on"],
        permissions=["lan.access", "power.control"],
    )
    assert load(tmp_path, raw).id == "power.adb"


def test_old_modules_still_load_on_1_5(tmp_path: Path) -> None:
    raw = discovery(
        id="power.adb",
        kind="power-driver",
        core=">=1.2 <2.0",
        capabilities=["power.on"],
        permissions=["lan.access", "power.control"],
    )
    assert load(tmp_path, raw).id == "power.adb"
    raw["core"] = ">=1.0 <2.0"
    raw.update(kind="storage", capabilities=[], permissions=[])
    assert load(tmp_path, raw).kind == "storage"


def test_discovery_module_refused_on_core_1_4(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="IncompatibleCore|requires"):
        load(tmp_path, discovery(), core="1.4.0")


def schema(addr: Any) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "host": {
                "type": "string",
                "default": "",
                "title": "t",
                "description": "d",
                "x-vds-level": "simple",
                "x-vds-device-address": addr,
            }
        },
    }


def test_device_address_annotation() -> None:
    validate_config_schema(schema("ip"))
    validate_config_schema(schema("mac"))
    for bad in ("hostname", "", None, 1):
        with pytest.raises(ConfigSchemaError):
            validate_config_schema(schema(bad))


def test_check_discoverer() -> None:
    class Good:
        scopes: ClassVar[frozenset[str]] = frozenset({"lan"})

        async def discover(self, scope: Any, net: Any, report: Any) -> None: ...

    class WrongScope(Good):
        scopes: ClassVar[frozenset[str]] = frozenset({"vlan"})

    check_discoverer(Good(), ["discovery.lan"])
    check_discoverer(object(), ["power.on"])  # rien de déclaré : rien à vérifier
    with pytest.raises(ContractError, match="module.discoverer_missing"):
        check_discoverer(object(), ["discovery.lan"])
    with pytest.raises(ContractError, match="module.discoverer_scope_mismatch"):
        check_discoverer(WrongScope(), ["discovery.lan"])


def test_ports_closed_list() -> None:
    assert set(DISCOVERY_PORTS) == {5555, 8001, 8002, 8008, 8009, 3000, 3001, 9197}
    assert ensure_discovery_ports([5555, 8009]) == (5555, 8009)
    for bad in (22, 80, 0):
        with pytest.raises(DiscoveryScopeError):
            ensure_discovery_ports([5555, bad])


def test_scan_args() -> None:
    sid = uuid4()
    a = DiscoveryScanArgs.model_validate({"scan_id": str(sid), "probe_ports": True})
    assert a.scope == "lan"
    for extra in ({"cidrs": ["10.0.0.0/8"]}, {"target": "1.2.3.4"}, {"scope": "wan"}):
        with pytest.raises(ValidationError):
            DiscoveryScanArgs.model_validate({"scan_id": str(sid), "probe_ports": True, **extra})
    with pytest.raises(ValidationError):
        DiscoveryScanArgs.model_validate({"scan_id": str(sid)})


@pytest.mark.parametrize(
    "mac",
    [
        "AA:BB:CC:DD:EE:FF",
        "aa-bb-cc-dd-ee-ff",
        "aa:bb:cc:dd:ee",
        "aabbccddeeff",
        "zz:bb:cc:dd:ee:ff",
    ],
)
def test_bad_mac_refused(mac: str) -> None:
    with pytest.raises(ValidationError):
        DiscoveredDevice.model_validate({"ip": "192.168.1.5", "mac": mac})


def test_device_bounds() -> None:
    DiscoveredDevice.model_validate({"ip": "192.168.1.5", "mac": "aa:bb:cc:dd:ee:ff"})
    for over in (
        {"name": "x" * 129},
        {"name": "a\nb"},
        {"suggested_drivers": ("a", "b", "c", "d", "e")},
        {"extra": 1},
        {"protocols": ["ssh"]},
    ):
        with pytest.raises(ValidationError):
            DiscoveredDevice.model_validate({"ip": "192.168.1.5", **over})


def test_batch_and_result() -> None:
    dev = {"ip": "192.168.1.5"}
    DiscoveryBatch.model_validate({"scan_id": str(uuid4()), "seq": 0, "devices": [dev] * 16})
    with pytest.raises(ValidationError):
        DiscoveryBatch.model_validate({"scan_id": str(uuid4()), "seq": 0, "devices": [dev] * 17})
    res = {
        "scan_id": str(uuid4()),
        "networks": ["192.168.1.0/24"],
        "truncated": False,
        "device_count": 3,
        "capped": False,
        "duration_ms": 1200,
    }
    DiscoveryResult.model_validate(res)
    with pytest.raises(ValidationError):
        DiscoveryResult.model_validate({**res, "device_count": 129})


def test_agent_command_registered_and_old_agent_unknown() -> None:
    assert AGENT_COMMAND_ARGS["discovery.scan"] is DiscoveryScanArgs
    cmd = Cmd.model_validate(
        {
            "cmd_id": str(uuid4()),
            "name": "discovery.scan",
            "args": {"scan_id": str(uuid4()), "probe_ports": False},
            "expires_at": "2026-10-30T12:00:00Z",
        }
    )
    assert isinstance(cmd.validate_for("agent"), DiscoveryScanArgs)
    # Agent 1.4 simulé : table de commandes sans discovery.scan => unknown_command.
    old = {k: v for k, v in AGENT_COMMAND_ARGS.items() if k != "discovery.scan"}
    assert "discovery.scan" not in old and cmd.name not in old
    with pytest.raises(ValueError, match="unknown_command"):
        cmd.model_copy(update={"name": "discovery.other"}).validate_for("agent")
    with pytest.raises(ValueError, match="unknown_command"):
        cmd.validate_for("player")
