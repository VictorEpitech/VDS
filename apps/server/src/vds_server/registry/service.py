"""Cycle de vie des modules : sync en table `module`, activation, désactivation, quarantaine."""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import (
    CORE_API_VERSION,
    ContractError,
    Event,
    Module,
    ModuleManifest,
    ScopedBus,
    parse_event,
)
from vds_sdk.versions import satisfies

from vds_server.core.mux import ModuleMux
from vds_server.db.models import Module as ModuleRow
from vds_server.events import CoreBus

from .discovery import Discovered, discover
from .runtime import load_server_module, make_context

log = logging.getLogger("vds.registry")

ACTIVATABLE = {"discovered", "disabled", "quarantined"}
DEFAULT_ENABLED_KINDS = {"content-type", "power-driver"}


@dataclass
class _Running:
    found: Discovered
    instance: Module | None


@dataclass
class ModuleRegistry:
    maker: async_sessionmaker[AsyncSession]
    bus: CoreBus
    modules_dir: Path
    required: tuple[str, ...] = ()
    core_version: str = CORE_API_VERSION
    startup_errors: dict[str, str] = field(default_factory=dict)
    # Branchements du noyau (facultatifs : absents dans les tests unitaires du registre).
    core_factory: Callable[[ModuleManifest], Any] | None = None
    config_factory: Callable[[ModuleManifest], Any] | None = None
    mux: ModuleMux | None = None
    _known: dict[str, Discovered] = field(default_factory=dict)
    _running: dict[str, _Running] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.bus.on_quarantine = self.quarantine

    # --- synchronisation -------------------------------------------------
    async def sync(self) -> None:
        self._known = {d.manifest.id: d for d in discover(self.modules_dir, self.core_version)}
        now = datetime.now(UTC)
        async with self.maker.begin() as s:
            rows = {r.id: r for r in (await s.execute(select(ModuleRow))).scalars()}
            for mid, d in self._known.items():
                row = rows.get(mid)
                m = d.manifest
                if row is None:
                    row = ModuleRow(id=mid, state="discovered", failure_count=0)
                    s.add(row)
                    row.state_changed_at = now
                row.version, row.kind, row.source = m.version, m.kind, "builtin"
                row.manifest = m.model_dump(mode="json", by_alias=True)
                row.config_schema = d.config_schema
                row.checksum = d.checksum
                if d.error:
                    self._set_state(row, "incompatible", d.error, now)
                elif row.state == "incompatible":
                    self._set_state(row, "discovered", None, now)

    @staticmethod
    def _set_state(row: ModuleRow, state: str, error: str | None, now: datetime) -> None:
        if row.state != state:
            row.state_changed_at = now
        row.state, row.last_error = state, error

    # --- démarrage / arrêt ----------------------------------------------
    async def start(self) -> None:
        await self.sync()
        async with self.maker() as s:
            rows = list((await s.execute(select(ModuleRow))).scalars())
        wanted = {r.id for r in rows if r.state == "enabled"}
        # Types de contenu et pilotes d'alimentation intégrés : actifs par défaut.
        # Un module désactivé ou en quarantaine par l'admin n'est jamais réactivé ici.
        wanted |= {
            r.id
            for r in rows
            if r.state == "discovered"
            and r.kind in DEFAULT_ENABLED_KINDS
            and r.id in self._known
            and not self._known[r.id].error
        }
        for mid in self.required:
            if mid not in self._known:
                self.startup_errors[mid] = "module.required_missing"
        wanted |= {m for m in self.required if m in self._known}
        pending = sorted(wanted & self._known.keys())
        while pending:  # ordre topologique naïf : un module attend ses `requires`
            progressed = False
            for mid in list(pending):
                reqs = self._known[mid].manifest.requires
                if all(r in self._running or r not in wanted for r in reqs):
                    pending.remove(mid)
                    progressed = True
                    try:
                        await self._activate(mid, force=True)
                    except ContractError as e:
                        self.startup_errors[mid] = e.code
            if not progressed:
                for mid in pending:
                    self.startup_errors[mid] = "module.dependency_missing"
                break

    async def stop(self) -> None:
        for mid in list(self._running):
            await self._deactivate_runtime(mid)

    # --- API de cycle de vie --------------------------------------------
    async def enable(self, module_id: str) -> None:
        await self._activate(module_id, force=False)

    async def disable(self, module_id: str) -> None:
        if module_id in self.required:
            raise ContractError("module.required_by_settings", module_id)
        for rid, run in self._running.items():
            if module_id in run.found.manifest.requires:
                raise ContractError("module.required_by", f"{module_id} <- {rid}")
        await self._deactivate_runtime(module_id)
        await self._persist(module_id, "disabled", None)
        await self.bus.publish(Event(name="module.disabled", payload={"module_id": module_id}))

    async def quarantine(self, module_id: str, error: str) -> None:
        if module_id not in self._known:
            return
        await self._deactivate_runtime(module_id)
        await self._persist(module_id, "quarantined", error, failure_count=self.bus.threshold)
        await self.bus.publish(
            Event(name="module.quarantined", payload={"module_id": module_id, "code": error})
        )

    def config_schema(self, module_id: str) -> dict[str, Any] | None:
        d = self._known.get(module_id)
        return d.config_schema if d else None

    def settings_entry(self, module_id: str) -> dict[str, Any] | None:
        """Réglages d'un module non-content : schéma de config, noms et traductions ; sinon None."""
        d = self._known.get(module_id)
        if d is None or d.error is not None or d.config_schema is None:
            return None
        if d.manifest.content is not None:
            return None
        i18n: dict[str, Any] = {}
        for locale in d.manifest.i18n:
            f = d.path / "i18n" / f"{locale}.json"
            if f.is_file():
                i18n[locale] = json.loads(f.read_text("utf-8"))
        return {
            "id": module_id,
            "version": d.manifest.version,
            "name": d.manifest.name.model_dump(),
            "description": d.manifest.description.model_dump(),
            "config_schema": d.config_schema,
            "i18n": i18n,
        }

    def content_item_schema(self, module_id: str) -> dict[str, Any] | None:
        """Schéma des éléments d'un content-type actif ; None si inconnu, inactif ou autre kind."""
        run = self._running.get(module_id)
        content = run.found.manifest.content if run else None
        if run is None or content is None:
            return None
        loaded: Any = json.loads((run.found.path / content.item_schema).read_text("utf-8"))
        return dict(loaded)

    def content_entry(self, module_id: str) -> dict[str, Any] | None:
        """Entrée player d'un content-type actif (version, élément, script, réseau) ; sinon None."""
        run = self._running.get(module_id)
        content = run.found.manifest.content if run else None
        if run is None or content is None:
            return None
        return {
            "version": run.found.manifest.version,
            "element": content.player.element,
            "script": f"modules/{module_id}/{content.player.script}",
            "requires_network": content.requires_network,
        }

    def content_types(self) -> list[dict[str, Any]]:
        """Content-types actifs : manifeste, schémas (élément, config), traductions, lus à chaud."""
        out: list[dict[str, Any]] = []
        for mid, run in sorted(self._running.items()):
            m, content = run.found.manifest, run.found.manifest.content
            if content is None:
                continue
            i18n: dict[str, Any] = {}
            for locale in m.i18n:
                f = run.found.path / "i18n" / f"{locale}.json"
                if f.is_file():
                    i18n[locale] = json.loads(f.read_text("utf-8"))
            item_schema: Any = json.loads((run.found.path / content.item_schema).read_text("utf-8"))
            out.append(
                {
                    "id": mid,
                    "version": m.version,
                    "name": m.name.model_dump(),
                    "description": m.description.model_dump(),
                    "element": content.player.element,
                    "accepts": content.media.accepts if content.media else [],
                    "requires_network": content.requires_network,
                    "item_schema": item_schema,
                    "config_schema": run.found.config_schema,
                    "i18n": i18n,
                }
            )
        return out

    def content_processor(self, mime: str) -> tuple[str, Module] | None:
        """Module content-type actif dont `content.media.accepts` couvre ce MIME."""
        for mid, run in sorted(self._running.items()):
            content = run.found.manifest.content
            if content is None or run.instance is None or not content.media:
                continue
            if mime in content.media.accepts:
                return mid, run.instance
        return None

    def instance(self, module_id: str) -> Module | None:
        run = self._running.get(module_id)
        return run.instance if run else None

    def is_running(self, module_id: str) -> bool:
        return module_id in self._running

    async def get(self, module_id: str) -> ModuleRow | None:
        async with self.maker() as s:
            return await s.get(ModuleRow, module_id)

    # --- interne ---------------------------------------------------------
    async def _activate(self, module_id: str, *, force: bool) -> None:
        d = self._known.get(module_id)
        if d is None:
            raise ContractError("module.not_found", module_id)
        row = await self.get(module_id)
        state = row.state if row else "discovered"
        if d.error or state == "incompatible":
            raise ContractError("module.incompatible", d.error or module_id)
        if module_id in self._running:
            return
        if not force and state not in ACTIVATABLE:
            raise ContractError("module.invalid_state", state)
        for rid, spec in d.manifest.requires.items():
            dep = self._running.get(rid)
            if dep is None or not satisfies(dep.found.manifest.version, spec):
                raise ContractError("module.dependency_missing", f"{module_id} -> {rid}")
        scoped = ScopedBus(self.bus.view(module_id), d.manifest)
        try:
            instance = load_server_module(d.path, d.manifest)
            if instance is not None:
                ctx = make_context(
                    d.manifest,
                    scoped,
                    core=self.core_factory(d.manifest) if self.core_factory else None,
                    config=self.config_factory(d.manifest) if self.config_factory else None,
                )
                await instance.setup(ctx)
                routes = instance.routes()
                if routes is not None and self.mux is not None:
                    if "http.routes" not in d.manifest.permissions:
                        raise ContractError("module.permission_denied", "http.routes")
                    if not isinstance(routes, APIRouter):
                        raise ContractError("module.routes_invalid", module_id)
                    self.mux.add(module_id, routes)
        except Exception as exc:
            self.bus.unsubscribe_all(module_id)
            code = exc.code if isinstance(exc, ContractError) else "module.setup_failed"
            await self._persist(module_id, "quarantined", code)
            await self.bus.publish(
                Event(name="module.quarantined", payload={"module_id": module_id, "code": code})
            )
            raise ContractError(code, module_id) from exc
        self._running[module_id] = _Running(d, instance)
        await self._persist(module_id, "enabled", None, failure_count=0)
        await self.bus.publish(Event(name="module.enabled", payload={"module_id": module_id}))

    async def _deactivate_runtime(self, module_id: str) -> None:
        self.bus.unsubscribe_all(module_id)
        if self.mux is not None:
            self.mux.remove(module_id)
        run = self._running.pop(module_id, None)
        if run and run.instance is not None:
            try:
                await run.instance.teardown()
            except Exception:
                log.warning("module.teardown_failed", extra={"module": module_id})

    async def _persist(
        self, module_id: str, state: str, error: str | None, *, failure_count: int | None = None
    ) -> None:
        async with self.maker.begin() as s:
            row = await s.get(ModuleRow, module_id)
            if row is None:
                raise ContractError("module.not_found", module_id)
            self._set_state(row, state, error, datetime.now(UTC))
            if failure_count is not None:
                row.failure_count = failure_count

    def agent_events(self) -> dict[str, str]:
        """Événements des modules actifs à cible site-agent : nom -> id du module."""
        out: dict[str, str] = {}
        for mid, d in self._known.items():
            if d.error is None and mid in self._running and "site-agent" in d.manifest.targets:
                for e in d.manifest.events.emits:
                    out[parse_event(e).name] = mid
        return out

    def agent_modules(self) -> list[tuple[str, str]]:
        """(id, version) des modules à cible site-agent, compatibles et actifs (état enabled)."""
        return [
            (mid, d.manifest.version)
            for mid, d in self._known.items()
            if d.error is None and mid in self._running and "site-agent" in d.manifest.targets
        ]

    def info(self) -> list[dict[str, Any]]:
        return [{"id": k, "running": k in self._running} for k in self._known]
