"""Aides de test pour les modules (contexte factice, état en mémoire, chargement)."""

import types
from pathlib import Path
from typing import Any

from vds_sdk import Event
from vds_sdk.agent import PowerDriver

from .modules import AgentContext, discover, import_in_module


class MemoryState:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class NullBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)

    def subscribe(self, name: str, handler: Any) -> Any:
        return lambda: None


def module_dir(module_id: str) -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "modules" / module_id / "module.json").exists():
            return p / "modules" / module_id
    raise FileNotFoundError(module_id)


def load_driver_class(module_id: str) -> type[PowerDriver]:
    d = next(x for x in discover(module_dir(module_id).parent) if x.manifest.id == module_id)
    mod_path, _, cls = d.manifest.entrypoints["site-agent"].partition(":")
    return getattr(import_in_module(d.path, module_id, mod_path), cls)  # type: ignore[no-any-return]


def load_fakes(module_id: str) -> types.ModuleType:
    return import_in_module(module_dir(module_id), module_id, "tests.fakes")  # type: ignore[no-any-return]


def make_ctx(module_id: str, state: MemoryState | None = None) -> AgentContext:
    from vds_sdk import ScopedBus

    d = next(x for x in discover(module_dir(module_id).parent) if x.manifest.id == module_id)
    return AgentContext(
        module_id, ScopedBus(NullBus(), d.manifest), state or MemoryState(), d.schema
    )
