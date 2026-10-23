"""Chargement du code d'un module intégré et contexte fourni à `setup`."""

import importlib.util
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vds_sdk import ContractError, Module, ModuleManifest, ScopedBus, ScopeRef


class _EmptyConfig:
    async def resolve(self, scope: ScopeRef | None = None) -> dict[str, Any]:
        return {}  # repli quand aucun lecteur de configuration n'est fourni


class _Core:
    pass


@dataclass
class Context:
    module_id: str
    bus: ScopedBus
    log: logging.Logger
    config: Any = None
    core: Any = None

    def t(self, key: str, locale: str, **params: Any) -> str:
        return key  # l'admin traduit ; le serveur ne renvoie que des clés


def make_context(
    manifest: ModuleManifest, bus: ScopedBus, *, core: Any = None, config: Any = None
) -> Context:
    return Context(
        module_id=manifest.id,
        bus=bus,
        log=logging.getLogger(f"vds.module.{manifest.id}"),
        config=config or _EmptyConfig(),
        core=core or _Core(),
    )


def load_server_module(root: Path, manifest: ModuleManifest) -> Module | None:
    """Instancie l'entrypoint `server` ; None pour un module déclaratif."""
    entry = manifest.entrypoints.get("server")
    if entry is None:
        return None
    dotted, _, cls_name = entry.partition(":")
    file = (root / Path(*dotted.split("."))).with_suffix(".py")
    if not file.resolve().is_relative_to(root.resolve()) or not file.is_file():
        raise ContractError("module.entrypoint_not_found", manifest.id)
    alias = "vds_mod_" + re.sub(r"\W", "_", manifest.id) + "_" + dotted.replace(".", "_")
    spec = importlib.util.spec_from_file_location(alias, file)
    if spec is None or spec.loader is None:
        raise ContractError("module.entrypoint_not_found", manifest.id)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    cls = getattr(mod, cls_name, None)
    if not (isinstance(cls, type) and issubclass(cls, Module)):
        raise ContractError("module.entrypoint_invalid", manifest.id)
    return cls()
