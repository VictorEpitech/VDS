"""Assemblage du serveur : moteur, bus, registre, pipeline d'authentification, routes de modules."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from vds_sdk import Event, Module, ModuleManifest
from vds_sdk.server import AuthProvider, StorageBackend

from vds_server.agents.auth import TOKEN_PREFIX, AgentAuth
from vds_server.agents.bridge import EventBridge
from vds_server.agents.cache import site_media_ids
from vds_server.agents.hub import AgentHub
from vds_server.agents.plan import PlanPusher
from vds_server.agents.queue import MODULES_KIND, CommandQueue, notify
from vds_server.commands.screenshots import CaptureSweeper
from vds_server.config.reader import ModuleConfigReader
from vds_server.config.registry_schemas import RegistrySchemaProvider
from vds_server.config.schemas import CORE_NAMESPACE
from vds_server.core.auth import AuthPipeline
from vds_server.core.errors import ApiError
from vds_server.core.facade import ModuleCore
from vds_server.core.mux import ModuleMux
from vds_server.db.base import make_engine, make_sessionmaker
from vds_server.db.models import Organization
from vds_server.diffusion.access import MediaAccess
from vds_server.diffusion.sweeper import ContentSweeper
from vds_server.discovery.service import DiscoveryEvents
from vds_server.enrollment.local import LocalChannel
from vds_server.events import CoreBus
from vds_server.feeds.access import FeedAccessCache
from vds_server.feeds.service import FeedService
from vds_server.net.safe_http import CoreSafeHttp
from vds_server.overrides.sweeper import SWEEP_INTERVAL_S, OverrideSweeper
from vds_server.player.device_auth import DeviceAuth, bearer
from vds_server.player.ratelimit import FailureLimiter
from vds_server.player.schedule import screen_media_ids
from vds_server.power.state import PowerStateTracker
from vds_server.provisioning.apk import ApkCatalog
from vds_server.provisioning.apk_update import ApkUpdateEvents, host_reader
from vds_server.provisioning.service import ProvisionEvents
from vds_server.provisioning.tickets import TicketBox
from vds_server.publication.backfill import ensure_published_baseline
from vds_server.registry import ModuleRegistry
from vds_server.settings import Settings
from vds_server.supervision import Supervisor

log = logging.getLogger("vds.runtime")

OUTBOX_POLL_S = 5.0


class Runtime:
    def __init__(self, settings: Settings, engine: AsyncEngine | None = None) -> None:
        self.settings = settings
        self.engine = engine or make_engine(settings.database_url)
        self.maker: async_sessionmaker[AsyncSession] = make_sessionmaker(self.engine)
        self.bus = CoreBus(
            self.maker,
            failure_threshold=settings.quarantine_failures,
            failure_window_s=settings.quarantine_window_s,
        )
        self.mux = ModuleMux()
        self.registry = ModuleRegistry(
            self.maker,
            self.bus,
            settings.modules_dir,
            required=(settings.auth_provider, settings.theme, settings.storage),
            core_factory=self._core_for,
            config_factory=self._config_for,
            mux=self.mux,
        )
        self.schemas = RegistrySchemaProvider(self.registry)
        self.pipeline = AuthPipeline(self.maker, self.bus, self.auth_provider)
        self.media_access = MediaAccess(self.pipeline)
        self.device_auth = DeviceAuth(self.maker)  # jeton d'appareil (player, médias)
        self.media_access.register_reader(self._device_reader)
        self.media_access.register_reader(self._agent_reader)
        self.agent_auth = AgentAuth(self.maker)  # jeton d'agent (WS, HTTP réservé à l'agent)
        self.agent_hub = AgentHub()
        self.local_channel = LocalChannel(settings.local_agent_dir)
        self.enroll_gate = asyncio.Semaphore(8)  # requêtes d'échange simultanées par worker
        self.enroll_limiter = FailureLimiter(limit=10, window_s=15 * 60)  # échecs par IP (/64)
        self.ticket_box = TicketBox(settings.secret_key) if settings.secret_key else None
        self.apk_catalog = ApkCatalog(settings.apk_dir, settings.apk_signer_sha256)
        self.command_queue = CommandQueue(self.maker)
        self.agent_bridge = EventBridge(self.maker, self.bus, self.registry.agent_events)
        self.provision_events = ProvisionEvents(self.maker, self.bus)
        self.apk_update_events = ApkUpdateEvents(self.maker, self.bus)
        self.apk_update_host = host_reader(self.maker, self.schemas)
        self.provision_events.register(self.bus.subscribe)
        self.discovery_events = DiscoveryEvents(self.maker, self.bus)
        self.agent_bridge.discovery_batch = self.discovery_events.on_batch
        self.core_config = ModuleConfigReader(self.maker, self.schemas, CORE_NAMESPACE).resolve
        self.supervisor = Supervisor(self.maker, self.bus, self.core_config)
        self.supervisor.register(self.bus.subscribe)
        self.power_state = PowerStateTracker(self.maker, self.schemas)
        self.power_state.register(self.bus.subscribe)
        self.override_sweeper = OverrideSweeper(self.maker, self.bus, self.schemas)
        self.content_sweeper = ContentSweeper(
            self.maker, self.bus, self.schemas, self._storage_by_id
        )
        self.screenshot_gate = asyncio.Semaphore(2)  # réencodages d'images simultanés par worker
        self.capture_sweeper = CaptureSweeper(
            self.maker, self.bus, self.schemas, self.storage_backend
        )
        self.safe_http = CoreSafeHttp(
            version=settings.version, public_origin=settings.public_origin
        )  # client sortant anti-SSRF des modules (permission net.outbound)
        self.feeds = FeedService(
            self.maker, self.bus, self.registry.instance, lambda _module_id: self.safe_http
        )
        self.feed_access = FeedAccessCache()
        for name in (
            "publication.approved",
            "module.enabled",
            "module.disabled",
            "module.quarantined",
            "screen.unpaired",
        ):
            self.bus.subscribe(name, self.feed_access.on_event)
        self.plan_pusher = PlanPusher(self.maker, self.schemas, self.command_queue)
        self.plan_pusher.register(self.bus.subscribe)
        for name in ("module.enabled", "module.disabled", "module.quarantined"):
            self.bus.subscribe(name, self._modules_changed)

    async def on_agent_result(
        self, cmd_id: UUID, status: str, code: str | None, data: dict[str, Any]
    ) -> None:
        """Résultat d'une commande d'agent : chaque suiveur ignore les commandes des autres."""
        await self.provision_events.on_cmd_result(cmd_id, status, code, data)
        await self.discovery_events.on_cmd_result(cmd_id, status, code, data)
        await self.apk_update_events.on_cmd_result(cmd_id, status, code, data)

    async def _modules_changed(self, _event: Event) -> None:
        """Propagé à tous les workers par NOTIFY (chacun ferme ses sessions d'agent)."""
        try:
            async with self.maker.begin() as s:
                await notify(s, MODULES_KIND, UUID(int=0))
        except Exception:
            log.warning("modules_changed notify failed, local refresh only")
            await self.agent_hub.refresh_all()

    async def _device_reader(self, request: Request) -> bool:
        """Jeton d'appareil valide => lecture des médias ; absent, inconnu ou révoqué => non."""
        if bearer(request) is None:
            return False
        try:
            device = await self.device_auth(request)
        except ApiError:
            return False
        raw = request.path_params.get("media_id")
        try:
            media_id = UUID(str(raw))
        except ValueError as exc:
            raise ApiError("media.not_found", 404) from exc
        async with self.maker() as session:
            if media_id not in await screen_media_ids(session, device.screen_id):
                if media_id in await screen_media_ids(
                    session, device.screen_id, include_expired=True
                ):
                    raise ApiError("media.not_found", 404)  # média expiré : plus servi
                raise ApiError("media.forbidden", 403)
        return True

    async def _agent_reader(self, request: Request) -> bool:
        """Jeton d'agent valide => lecture des seuls médias programmés sur son site."""
        token = bearer(request)
        if token is None or not token.startswith(TOKEN_PREFIX):
            return False
        try:
            principal = await self.agent_auth(request)
        except ApiError:
            return False
        try:
            media_id = UUID(str(request.path_params.get("media_id")))
        except ValueError as exc:
            raise ApiError("media.not_found", 404) from exc
        async with self.maker() as session:
            if media_id not in await site_media_ids(session, principal.site_id):
                if media_id in await site_media_ids(
                    session, principal.site_id, include_expired=True
                ):
                    raise ApiError("media.not_found", 404)  # média expiré : plus servi
                raise ApiError("media.forbidden", 403)
        return True

    def _core_for(self, manifest: ModuleManifest) -> ModuleCore:
        return ModuleCore(self, manifest)

    def _config_for(self, manifest: ModuleManifest) -> ModuleConfigReader:
        return ModuleConfigReader(self.maker, self.schemas, manifest.id)

    def _storage_by_id(self, module_id: str) -> StorageBackend | None:
        instance: Module | None = self.registry.instance(module_id)
        return instance if isinstance(instance, StorageBackend) else None

    def storage_backend(self) -> StorageBackend | None:
        instance: Module | None = self.registry.instance(self.settings.storage)
        return instance if isinstance(instance, StorageBackend) else None

    def auth_provider(self) -> AuthProvider | None:
        instance: Module | None = self.registry.instance(self.settings.auth_provider)
        return instance if isinstance(instance, AuthProvider) else None

    async def session(self) -> AsyncIterator[AsyncSession]:
        """Dépendance FastAPI : une session par requête."""
        async with self.maker() as session:
            yield session

    async def ensure_organization(self) -> None:
        async with self.maker.begin() as s:
            if (await s.execute(select(Organization.id))).first() is None:
                s.add(Organization(name=self.settings.organization_name))

    def startup_ok(self) -> bool:
        return not self.registry.startup_errors

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        await self.ensure_organization()
        await ensure_published_baseline(self.maker)  # avant de servir les players
        await self.registry.start()
        await self.apk_catalog.load()
        self.agent_hub.start(self.engine)
        self.supervisor.start()
        self.power_state.start()

        async def pump() -> None:
            while True:
                await asyncio.sleep(OUTBOX_POLL_S)
                with contextlib.suppress(Exception):
                    await self.bus.process_pending()

        async def sweep() -> None:
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_S)
                await self.override_sweeper.run_once()
                await self.content_sweeper.run_once()
                await self.capture_sweeper.run_once()

        task = asyncio.create_task(pump())
        sweep_task = asyncio.create_task(sweep())
        feeds_task = asyncio.create_task(self.feeds.run_forever())
        try:
            app.state.runtime = self
            yield
        finally:
            task.cancel()
            sweep_task.cancel()
            feeds_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with contextlib.suppress(asyncio.CancelledError):
                await sweep_task
            with contextlib.suppress(asyncio.CancelledError):
                await feeds_task
            await self.power_state.stop()
            await self.supervisor.stop()
            await self.agent_hub.stop()
            await self.registry.stop()
            await self.engine.dispose()
