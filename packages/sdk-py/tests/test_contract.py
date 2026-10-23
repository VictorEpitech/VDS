import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from vds_sdk import (
    ConfigSchemaError,
    Event,
    IncompatibleCore,
    ModuleManifest,
    ScopedBus,
    UndeclaredEvent,
    load_manifest,
    validate_config_schema,
)
from vds_sdk.bus import EventHandler
from vds_sdk.manifest import ModuleManifest as MM
from vds_sdk.server import AuthProvider

ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = ROOT / ".claude/skills/vds-module/templates"


def valid() -> dict[str, Any]:
    return {
        "id": "power.samsung-ws",
        "version": "1.2.0",
        "core": ">=1.0 <2.0",
        "kind": "power-driver",
        "name": {"fr": "a", "en": "b"},
        "description": {"fr": "a", "en": "b"},
        "targets": ["server", "site-agent"],
        "entrypoints": {"server": "server.module:D", "site-agent": "agent.module:D"},
        "permissions": ["lan.access"],
        "events": {"listens": ["power.requested"], "emits": ["power.changed@2"]},
        "author": "V-Tech",
    }


def test_valid_manifest() -> None:
    m = ModuleManifest.model_validate(valid())
    m.check_core_compat()


def test_template_module_json_valid(tmp_path: Path) -> None:
    raw = json.loads((TEMPLATES / "module.json").read_text())
    raw["id"] = "power.exemple"
    raw["kind"] = "power-driver"
    (tmp_path / "module.json").write_text(json.dumps(raw))
    (tmp_path / "config.schema.json").write_text((TEMPLATES / "config.schema.json").read_text())
    assert load_manifest(tmp_path / "module.json").id == "power.exemple"


def test_template_config_schema_valid() -> None:
    validate_config_schema(json.loads((TEMPLATES / "config.schema.json").read_text()))


def test_missing_level_refused() -> None:
    schema = json.loads((TEMPLATES / "config.schema.json").read_text())
    del schema["properties"]["enabled"]["x-vds-level"]
    with pytest.raises(ConfigSchemaError) as e:
        validate_config_schema(schema)
    assert e.value.code == "module.config_schema.level_missing"


def test_secret_requires_env() -> None:
    schema = json.loads((TEMPLATES / "config.schema.json").read_text())
    schema["properties"]["enabled"]["x-vds-secret"] = True
    with pytest.raises(ConfigSchemaError):
        validate_config_schema(schema)
    schema["properties"]["enabled"]["x-vds-env"] = "X"
    validate_config_schema(schema)


def test_bad_scopes_refused() -> None:
    schema = json.loads((TEMPLATES / "config.schema.json").read_text())
    schema["properties"]["enabled"]["x-vds-scopes"] = ["planet"]
    with pytest.raises(ConfigSchemaError):
        validate_config_schema(schema)


def test_unknown_kind_refused() -> None:
    d = valid() | {"kind": "gizmo"}
    with pytest.raises(ValidationError):
        MM.model_validate(d)


def test_unknown_permission_refused() -> None:
    with pytest.raises(ValidationError):
        MM.model_validate(valid() | {"permissions": ["root"]})


def test_bad_event_name_refused() -> None:
    with pytest.raises(ValidationError):
        MM.model_validate(valid() | {"events": {"emits": ["Bad Name"]}})


def test_incompatible_core() -> None:
    m = MM.model_validate(valid() | {"core": ">=2.0 <3.0"})
    with pytest.raises(IncompatibleCore):
        m.check_core_compat()


def test_theme_declarative() -> None:
    d = valid() | {"id": "theme.x", "kind": "theme", "targets": ["admin"], "entrypoints": {}}
    with pytest.raises(ValidationError):
        MM.model_validate(d)
    d["theme"] = {
        "css": "assets/t.css",
        "logo": {"light": "a.svg", "dark": "b.svg"},
        "icons": "lucide",
        "color_schemes": ["light"],
    }
    MM.model_validate(d)


def test_auth_provider_kind_requires_entrypoint() -> None:
    d = valid() | {"id": "auth.local", "kind": "auth-provider", "entrypoints": {}}
    with pytest.raises(ValidationError):
        MM.model_validate(d)
    assert AuthProvider.__abstractmethods__ >= {"authenticate", "login_url", "logout_url"}


class FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)

    def subscribe(self, name: str, handler: EventHandler) -> Any:
        return lambda: None


async def test_scoped_bus_enforces_declarations() -> None:
    raw = FakeBus()
    bus = ScopedBus(raw, MM.model_validate(valid()))
    await bus.publish("power.changed", {"on": True}, version=2)
    assert raw.published[0].source == "power.samsung-ws"
    with pytest.raises(UndeclaredEvent):
        await bus.publish("power.requested")
    with pytest.raises(UndeclaredEvent):
        bus.subscribe("screen.created", lambda e: None)  # type: ignore[arg-type,return-value]
    bus.subscribe("power.requested", lambda e: None)  # type: ignore[arg-type,return-value]


def test_published_schema_is_up_to_date() -> None:
    from vds_sdk.gen_schema import PATH, render

    assert PATH.read_text(encoding="utf-8") == render()


def test_users_provision_only_for_auth_provider() -> None:
    raw = valid()
    raw["permissions"] = ["users.provision"]
    with pytest.raises(ValidationError, match="module.permission_kind_forbidden"):
        ModuleManifest.model_validate(raw)
    raw["kind"] = "auth-provider"
    assert "users.provision" in ModuleManifest.model_validate(raw).permissions
