import json
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import ValidationError
from vds_sdk import (
    CORE_EVENT_PAYLOAD_1_2,
    CORE_EVENTS_1_1,
    CORE_EVENTS_1_2,
    Event,
    load_manifest,
    parse_event,
    permissions,
)
from vds_sdk.agent import DetectHint, PowerDriver, PowerResult, PowerState, PowerTarget, StateStore
from vds_sdk.manifest import ModuleManifest
from vds_sdk.server import DeliveryResult, Notification, Notifier, Recipient


def power_manifest(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "power.wol",
        "version": "1.0.0",
        "core": ">=1.2 <2.0",
        "kind": "power-driver",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["site-agent"],
        "entrypoints": {"site-agent": "m:D"},
        "capabilities": ["power.on"],
        "permissions": ["lan.access", "power.control"],
        "author": "V-Tech",
    }
    raw.update(over)
    return raw


def notifier_manifest(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": "notify.mail",
        "version": "1.0.0",
        "core": ">=1.2 <2.0",
        "kind": "notifier",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["server"],
        "entrypoints": {"server": "m:N"},
        "permissions": ["incidents.read"],
        "events": {"listens": ["incident.opened"]},
        "author": "V-Tech",
    }
    raw.update(over)
    return raw


def test_valid_power_driver_and_notifier() -> None:
    ModuleManifest.model_validate(power_manifest())
    ModuleManifest.model_validate(power_manifest(targets=["player"], entrypoints={}))
    ModuleManifest.model_validate(notifier_manifest())


@pytest.mark.parametrize("core", [">=1.0 <2.0", ">=1.1 <2.0", ">=1.0 <1.2"])
@pytest.mark.parametrize("factory", [power_manifest, notifier_manifest])
def test_kind_requires_1_2(core: str, factory: Any) -> None:
    with pytest.raises(ValidationError, match="contract.kind_requires_1_2"):
        ModuleManifest.model_validate(factory(core=core))


def test_power_driver_rules() -> None:
    with pytest.raises(ValidationError, match="power_driver_target_invalid"):
        ModuleManifest.model_validate(
            power_manifest(targets=["server"], entrypoints={"server": "m:D"})
        )
    with pytest.raises(ValidationError, match="power_driver_capability_invalid"):
        ModuleManifest.model_validate(power_manifest(capabilities=["power.fly"]))


def test_notifier_rules() -> None:
    with pytest.raises(ValidationError, match="notifier_target_invalid"):
        ModuleManifest.model_validate(
            notifier_manifest(
                targets=["server", "site-agent"],
                entrypoints={"server": "m:N", "site-agent": "m:N"},
            )
        )


def test_load_manifest_12(tmp_path: Path) -> None:
    (tmp_path / "module.json").write_text(json.dumps(power_manifest()))
    assert load_manifest(tmp_path / "module.json").id == "power.wol"


def test_new_permissions() -> None:
    for p in ("power.control", "power.calendars.write", "incidents.read"):
        assert p in permissions.PERMISSIONS


def test_event_origin_default() -> None:
    assert Event(name="a.b").origin == "server"
    assert Event(name="a.b", origin="agent:s1").origin == "agent:s1"


def test_events_1_2() -> None:
    assert CORE_EVENTS_1_1 < CORE_EVENTS_1_2
    expected = {
        "agent.enrolled", "agent.rotated", "agent.revoked", "agent.connected",
        "agent.disconnected", "power.requested", "power.changed", "power.failed",
        "power.plan_changed", "screen.status_changed", "incident.opened",
        "incident.resolved", "cache.synced",
    }  # fmt: skip
    assert expected <= CORE_EVENTS_1_2
    assert expected <= set(CORE_EVENT_PAYLOAD_1_2)
    for name in CORE_EVENTS_1_2:
        parse_event(name)


def test_power_driver_interface() -> None:
    class D(PowerDriver):
        capabilities = frozenset({"power.on"})

        async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult:
            return PowerResult(ok=True, state="on", verified=False)

        async def get_state(self, target: PowerTarget) -> PowerState:
            return "unknown"

    d = D()
    assert D.capabilities == {"power.on"}
    import asyncio

    assert asyncio.run(d.detect(DetectHint())) is None


def test_notifier_models() -> None:
    assert Recipient(email="a@b.fr").locale == "fr"
    assert DeliveryResult(ok=True).code is None
    assert Notification.model_fields["transition"] is not None
    assert Notifier.__abstractmethods__ == {"send"}
    assert StateStore is not None
