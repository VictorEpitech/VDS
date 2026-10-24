"""Découverte et chargement des modules de l'agent (`modules/*/module.json`, target site-agent)."""

import importlib
import json
import logging
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from vds_sdk import CORE_API_VERSION, ModuleManifest, ScopedBus, ScopeRef
from vds_sdk.agent import PowerDriver, StateStore

log = logging.getLogger("vds.agent.modules")


def package_name(module_id: str) -> str:
    return "vds_mod_" + module_id.replace(".", "_").replace("-", "_")


def load_package(module_dir: Path, module_id: str) -> str:
    """Enregistre le dossier du module comme paquet synthétique ; retourne son nom."""
    name = package_name(module_id)
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(module_dir)]
        sys.modules[name] = pkg
    return name


def import_in_module(module_dir: Path, module_id: str, dotted: str) -> Any:
    return importlib.import_module(f"{load_package(module_dir, module_id)}.{dotted}")


@dataclass
class Discovered:
    path: Path
    manifest: ModuleManifest
    schema: dict[str, Any] | None
    error: str | None = None


def discover(modules_dir: Path) -> list[Discovered]:
    out: list[Discovered] = []
    for mp in sorted(modules_dir.glob("*/module.json")):
        try:
            manifest = ModuleManifest.model_validate(json.loads(mp.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            log.error("module.manifest_invalid", extra={"path": str(mp)})
            continue
        if "site-agent" not in manifest.targets:
            continue
        if manifest.id.startswith("agent."):  # espace d'état réservé au cœur de l'agent
            log.error("module.id_reserved", extra={"path": str(mp)})
            continue
        schema = None
        error = None
        try:
            manifest.check_core_compat(CORE_API_VERSION)
            if manifest.config is not None:
                schema = json.loads((mp.parent / manifest.config.ref).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - code stable remonté dans le heartbeat
            error = getattr(e, "code", "module.incompatible")
        out.append(Discovered(mp.parent, manifest, schema, error))
    return out


def apply_defaults(schema: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    """Complète avec les `default` du schéma puis valide ; ValueError('module.config_invalid')."""
    if schema is None:
        return dict(config)
    merged = {k: p["default"] for k, p in schema.get("properties", {}).items() if "default" in p}
    merged.update(config)
    try:
        jsonschema.validate(merged, schema)
    except jsonschema.ValidationError as e:
        raise ValueError("module.config_invalid") from e
    return merged


class _ConfigReader:
    def __init__(self, owner: "AgentContext") -> None:
        self._o = owner

    async def resolve(self, scope: ScopeRef | None = None) -> dict[str, Any]:
        if scope is not None and scope.level == "screen" and scope.id:
            return dict(self._o.screen_configs.get(scope.id, {}))
        return {}


class AgentContext:
    """Implémente `AgentModuleContext` (ModuleContext + state)."""

    def __init__(
        self, module_id: str, bus: ScopedBus, state: StateStore, schema: dict[str, Any] | None
    ) -> None:
        self.module_id = module_id
        self.bus = bus
        self.state = state
        self.schema = schema
        self.config = _ConfigReader(self)
        self.core: Any = types.SimpleNamespace()
        self.log = logging.getLogger(f"vds.module.{module_id}")
        self.screen_configs: dict[str, dict[str, Any]] = {}

    def t(self, key: str, locale: str, **params: Any) -> str:
        return key


@dataclass
class LoadedDriver:
    manifest: ModuleManifest
    driver: PowerDriver
    ctx: AgentContext
    status: str = "ok"
    code: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def instantiate(d: Discovered, bus: Any, state: StateStore) -> LoadedDriver:
    m = d.manifest
    ep = m.entrypoints["site-agent"]
    mod_path, _, cls_name = ep.partition(":")
    mod = import_in_module(d.path, m.id, mod_path)
    driver = getattr(mod, cls_name)()
    ctx = AgentContext(m.id, ScopedBus(bus, m), state, d.schema)
    await driver.setup(ctx)
    return LoadedDriver(m, driver, ctx)
