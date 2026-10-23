"""Assemblage du serveur : moteur, bus, registre, pipeline d'authentification, routes de modules."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from vds_sdk import Module, ModuleManifest
from vds_sdk.server import AuthProvider, StorageBackend

from vds_server.config.reader import ModuleConfigReader
from vds_server.config.registry_schemas import RegistrySchemaProvider
from vds_server.core.auth import AuthPipeline
from vds_server.core.errors import ApiError
from vds_server.core.facade import ModuleCore
from vds_server.core.mux import ModuleMux
from vds_server.db.base import make_engine, make_sessionmaker
from vds_server.db.models import Organization
from vds_server.diffusion.access import MediaAccess
from vds_server.events import CoreBus
from vds_server.player.device_auth import DeviceAuth, bearer
from vds_server.player.schedule import screen_media_ids
from vds_server.registry import ModuleRegistry
from vds_server.settings import Settings

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
                raise ApiError("media.forbidden", 403)
        return True

    def _core_for(self, manifest: ModuleManifest) -> ModuleCore:
        return ModuleCore(self, manifest)

    def _config_for(self, manifest: ModuleManifest) -> ModuleConfigReader:
        return ModuleConfigReader(self.maker, self.schemas, manifest.id)

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
        await self.registry.start()

        async def pump() -> None:
            while True:
                await asyncio.sleep(OUTBOX_POLL_S)
                with contextlib.suppress(Exception):
                    await self.bus.process_pending()

        task = asyncio.create_task(pump())
        try:
            app.state.runtime = self
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await self.registry.stop()
            await self.engine.dispose()
