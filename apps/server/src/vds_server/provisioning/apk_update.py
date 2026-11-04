"""Mise à jour de l'APK player (vds-core 1.6.0) : éligibilité, commande `apk.update`, résultat.

Le noyau ne connaît pas la plateforme : il émet une commande fermée vers l'agent du site, qui
route vers le module déclarant `update.<plateforme>`. L'admin n'envoie ni adresse, ni sha, ni
taille : la référence d'APK (`ApkRef`, jamais d'URL) est ajoutée ici, côté serveur.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import Event, EventBus, ScopeRef
from vds_sdk.agent.provision import ApkUpdateOutcome, ApkUpdateRequest
from vds_sdk.versions import Version

from vds_server.agents.queue import CommandQueue
from vds_server.agents.service import agent_status
from vds_server.config.reader import ModuleConfigReader
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Screen, ScreenDevice, Site
from vds_server.db.models_power import RemoteCommand, SiteAgent
from vds_server.provisioning.apk import ApkCatalog

log = logging.getLogger("vds.apk_update")

CMD_NAME = "apk.update"
CMD_TTL_S = 300
MIN_CORE = Version(1, 6, 0)
ADB_NAMESPACE = "power.adb"
PLATFORM = "android"

State = Literal["up_to_date", "outdated", "unknown", "not_android"]
Reason = Literal[
    "agent_offline",
    "agent_outdated",
    "module_disabled",
    "no_adb_host",
    "apk_unavailable",
    "not_android",
    "up_to_date",
    "update_running",
]

_CODE_RE = re.compile(r"[a-z0-9_.]{1,96}")

# (id, version, plateformes) des modules site-agent actifs qui déclarent `update.<plateforme>`
UpdateModules = Callable[[], list[tuple[str, str, frozenset[str]]]]


# --- schémas d'API ----------------------------------------------------------------------------
class ApkUpdateArgs(BaseModel):
    """Arguments acceptés de l'admin pour `apk.update` : rien d'autre que l'identifiant."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class AvailableApkOut(BaseModel):
    version_code: int
    version_name: str
    source: Literal["image", "dir"]


class ScreenApkOut(BaseModel):
    screen_id: UUID
    installed_version_code: int | None
    installed_version_name: str | None
    state: State
    updatable: bool
    reason: Reason | None


class FleetApkOut(BaseModel):
    available: AvailableApkOut | None
    screens: list[ScreenApkOut]


# --- éligibilité (fonction pure) ---------------------------------------------------------------
def agent_supports(
    last_hello: object,
    modules: list[tuple[str, str, frozenset[str]]],
    platform: str,
    min_core: Version = MIN_CORE,
) -> Literal["ok", "agent_outdated", "module_disabled"]:
    hello = last_hello if isinstance(last_hello, dict) else {}
    try:
        if Version.parse(str(hello.get("core", "0"))) < min_core:
            return "agent_outdated"
    except ValueError:
        return "agent_outdated"
    wanted = [(mid, v) for mid, v, plats in modules if platform in plats]
    if not wanted:
        return "module_disabled"
    reported = {
        m.get("id"): m.get("version") for m in hello.get("modules", []) if isinstance(m, dict)
    }
    for mid, version in wanted:
        try:
            if mid in reported and Version.parse(str(reported[mid])) >= Version.parse(version):
                return "ok"
        except ValueError:
            continue
    return "agent_outdated"


def state_of(
    installed: int | None, available: int | None, is_android: bool, confirmed: bool = False
) -> State:
    """`installed` est déclaré par l'appareil (indicatif) ; `confirmed` = écrit par l'agent.

    Un code déclaré >= disponible sans confirmation reste `unknown` : l'étape inspect de
    l'agent arbitre (la mise à jour n'est pas bloquée).
    """
    if not is_android:
        return "not_android"
    if installed is None or available is None:
        return "unknown"
    if installed < available:
        return "outdated"
    return "up_to_date" if confirmed else "unknown"


def reason_of(
    *,
    is_android: bool,
    running: bool,
    state: State,
    apk_available: bool,
    agent: SiteAgent | None,
    modules: list[tuple[str, str, frozenset[str]]],
    host: object,
) -> Reason | None:
    """Première raison bloquante, dans l'ordre : écran, APK, agent, module, adresse."""
    if not is_android:
        return "not_android"
    if running:
        return "update_running"
    if state == "up_to_date":
        return "up_to_date"
    if not apk_available:
        return "apk_unavailable"
    if agent is None or agent_status(agent) != "online":
        return "agent_offline"
    support = agent_supports(agent.last_hello, modules, PLATFORM)
    if support != "ok":
        return support
    if not (isinstance(host, str) and host.strip()):
        return "no_adb_host"
    return None


def _now() -> datetime:
    return datetime.now(UTC)


class ApkUpdateService:
    def __init__(
        self,
        session: AsyncSession,
        bus: EventBus | None,
        actor: Principal,
        queue: CommandQueue,
        catalog: ApkCatalog,
        modules: UpdateModules,
        host_of: Callable[[UUID], Any],
    ) -> None:
        self._s, self._bus, self._actor, self._queue = session, bus, actor, queue
        self._catalog, self._modules, self._host_of = catalog, modules, host_of

    async def _agents(self, site_ids: set[UUID]) -> dict[UUID, SiteAgent]:
        stmt = select(SiteAgent).where(
            SiteAgent.site_id.in_(site_ids), SiteAgent.revoked_at.is_(None)
        )
        return {a.site_id: a for a in (await self._s.execute(stmt)).scalars()}

    async def _running(self, screen_ids: set[UUID]) -> set[UUID]:
        stmt = select(RemoteCommand.screen_id).where(
            RemoteCommand.name == CMD_NAME,
            RemoteCommand.screen_id.in_(screen_ids),
            RemoteCommand.status.in_(("queued", "sent")),
            RemoteCommand.expires_at > _now(),
        )
        return {sid for sid in (await self._s.execute(stmt)).scalars() if sid is not None}

    async def _devices(self, screen_ids: set[UUID]) -> dict[UUID, ScreenDevice]:
        stmt = select(ScreenDevice).where(
            ScreenDevice.screen_id.in_(screen_ids), ScreenDevice.revoked_at.is_(None)
        )
        return {d.screen_id: d for d in (await self._s.execute(stmt)).scalars()}

    async def _eligibility(self, screens: list[Screen]) -> list[ScreenApkOut]:
        await self._catalog.refresh()
        apk = self._catalog.latest()
        ids = {s.id for s in screens}
        devices = await self._devices(ids)
        running = await self._running(ids)
        agents = await self._agents({s.site_id for s in screens})
        modules = self._modules()
        out: list[ScreenApkOut] = []
        for screen in screens:
            dev = devices.get(screen.id)
            is_android = dev is not None and dev.platform == PLATFORM
            installed = dev.app_version_code if dev is not None and is_android else None
            confirmed = (
                dev is not None
                and installed is not None
                and dev.agent_version_code == installed  # écrit par l'agent, pas modifié depuis
            )
            state = state_of(installed, apk.version_code if apk else None, is_android, confirmed)
            host = await self._host_of(screen.id) if is_android else None
            reason = reason_of(
                is_android=is_android,
                running=screen.id in running,
                state=state,
                apk_available=apk is not None,
                agent=agents.get(screen.site_id),
                modules=modules,
                host=host,
            )
            out.append(
                ScreenApkOut(
                    screen_id=screen.id,
                    installed_version_code=installed,
                    installed_version_name=dev.app_version
                    if dev is not None and is_android
                    else None,
                    state=state,
                    updatable=reason is None,
                    reason=reason,
                )
            )
        return out

    async def fleet(self, site_id: UUID | None) -> FleetApkOut:
        if site_id is not None:
            site = await self._s.get(Site, site_id)
            if site is None or site.archived_at is not None:
                raise ApiError("site.not_found", 404)
        stmt = select(Screen).where(Screen.archived_at.is_(None)).order_by(Screen.created_at)
        if site_id is not None:
            stmt = stmt.where(Screen.site_id == site_id)
        screens = list((await self._s.execute(stmt)).scalars())
        rows = await self._eligibility(screens) if screens else []
        await self._catalog.refresh()
        apk = self._catalog.latest()
        available = (
            AvailableApkOut(
                version_code=apk.version_code,
                version_name=apk.version_name,
                source=self._catalog.source,
            )
            if apk
            else None
        )
        return FleetApkOut(available=available, screens=rows)

    # --- commande -------------------------------------------------------------------------
    async def _existing(self, agent_id: UUID, request_id: UUID) -> RemoteCommand | None:
        stmt = select(RemoteCommand).where(
            RemoteCommand.target_kind == "agent",
            RemoteCommand.target_id == agent_id,
            RemoteCommand.name == CMD_NAME,
            RemoteCommand.args["request_id"].astext == str(request_id),
        )
        return (await self._s.execute(stmt)).scalars().first()

    async def request(
        self, site_id: UUID, agent_id: UUID, screen_id: UUID | None, args: dict[str, Any]
    ) -> RemoteCommand:
        agent = await self._s.get(SiteAgent, agent_id)
        if agent is None or agent.site_id != site_id:
            raise ApiError("agents.not_found", 404)
        if agent.revoked_at is not None:
            raise ApiError("agents.revoked", 409)
        try:
            parsed = ApkUpdateArgs.model_validate(args)
        except ValidationError as exc:
            raise ApiError("agents.command_args_invalid", 422) from exc
        if screen_id is None:
            raise ApiError("agents.command_screen_required", 422)
        screen = await self._s.get(Screen, screen_id)
        if screen is None or screen.site_id != site_id:
            raise ApiError("agents.screen_not_in_site", 403)
        # sérialise les demandes d'un même écran, puis d'un même request_id (verrous levés au
        # commit) ; ordre fixe écran -> request_id : pas d'interblocage
        for key in (f"apkupd:{screen_id}", f"apkupd-rid:{parsed.request_id}"):
            await self._s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key}
            )
        prior = await self._existing(agent_id, parsed.request_id)
        if prior is not None:  # idempotence : même request_id, même écran => même commande
            if prior.screen_id != screen_id:
                raise ApiError("agents.request_id_conflict", 409)
            await self._s.rollback()  # lève le verrou ; recharge la ligne expirée
            await self._s.refresh(prior)
            return prior
        (row,) = await self._eligibility([screen])
        if row.reason == "update_running":
            raise ApiError("update_running", 409)
        if row.reason == "up_to_date":
            raise ApiError("up_to_date", 409)
        if row.reason == "apk_unavailable":
            raise ApiError("apk_unavailable", 503)
        if row.reason is not None:
            raise ApiError(row.reason, 422)
        apk = self._catalog.latest()
        if apk is None:  # garanti par l'éligibilité, repli défensif
            raise ApiError("apk_unavailable", 503)
        req = ApkUpdateRequest(
            request_id=parsed.request_id,
            screen_id=screen_id,
            platform=PLATFORM,
            apk=apk.ref(),
        )
        cmd = await self._queue.enqueue(
            "agent",
            agent_id,
            CMD_NAME,
            screen_id=screen_id,
            args=req.model_dump(mode="json"),
            ttl_s=CMD_TTL_S,
            created_by=self._actor.user_id,
        )
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=self._actor.actor_id,
            action="screen.apk_update.requested",
            target_type="screen",
            target_id=str(screen_id),
            details={
                "site_id": str(site_id),
                "request_id": str(parsed.request_id),
                "from_version_code": row.installed_version_code,
                "to_version_code": apk.version_code,
            },
        )
        await self._s.commit()
        return cmd


def host_reader(
    maker: async_sessionmaker[AsyncSession], schemas: ConfigSchemaProvider
) -> Callable[[UUID], Any]:
    """`power.adb.host` résolu de l'écran (config héritée, source unique de l'adresse)."""
    reader = ModuleConfigReader(maker, schemas, ADB_NAMESPACE)

    async def host_of(screen_id: UUID) -> object:
        cfg = await reader.resolve(ScopeRef(level="screen", id=str(screen_id)))
        return cfg.get("host")

    return host_of


class ApkUpdateEvents:
    """Résultat final de la commande `apk.update` : version installée, audit, événements."""

    def __init__(self, maker: async_sessionmaker[AsyncSession], bus: EventBus) -> None:
        self._maker, self._bus = maker, bus

    async def on_cmd_result(
        self, cmd_id: UUID, status: str, code: str | None, data: dict[str, Any]
    ) -> None:
        events: list[Event] = []
        async with self._maker() as s:
            cmd = await s.get(RemoteCommand, cmd_id)
            if cmd is None or cmd.name != CMD_NAME or cmd.screen_id is None:
                return
            try:
                req = ApkUpdateRequest.model_validate(cmd.args)
            except ValidationError:
                return
            outcome: ApkUpdateOutcome | None = None
            if status == "ok":
                try:
                    outcome = ApkUpdateOutcome.model_validate(data)
                except ValidationError:
                    outcome = None  # M4 : sortie invalide de l'agent = échec
            ok = outcome is not None and outcome.ok
            fail = (outcome.code if outcome else None) or code
            if not (isinstance(fail, str) and _CODE_RE.fullmatch(fail)):
                fail = "update.failed"
            if outcome is not None and outcome.installed_version_code:
                # sans attendre le heartbeat du player
                await s.execute(
                    text(
                        "UPDATE screen_device SET app_version_code = :v, agent_version_code = :v"
                        " WHERE screen_id = :sid AND revoked_at IS NULL"
                    ),
                    {"v": outcome.installed_version_code, "sid": cmd.screen_id},
                )
            result_code = (outcome.code if outcome else None) if ok else fail
            audit.record(
                s,
                actor_kind="system",
                actor_id=None,
                action="screen.apk_update.finished",
                target_type="screen",
                target_id=str(cmd.screen_id),
                details={
                    "request_id": str(req.request_id),
                    "status": "ok" if ok else "failed",
                    "code": result_code,
                },
            )
            if ok and outcome is not None:
                if outcome.code != "update.android.already_up_to_date":
                    events.append(
                        Event(
                            name="screen.apk_updated",
                            payload={
                                "request_id": str(req.request_id),
                                "screen_id": str(cmd.screen_id),
                                "previous_version_code": outcome.previous_version_code,
                                "version_code": outcome.installed_version_code
                                or req.apk.version_code,
                            },
                            source="core",
                        )
                    )
            else:
                events.append(
                    Event(
                        name="screen.apk_update_failed",
                        payload={
                            "request_id": str(req.request_id),
                            "screen_id": str(cmd.screen_id),
                            "version_code": req.apk.version_code,
                            "code": fail,
                        },
                        source="core",
                    )
                )
            await s.commit()
        for event in events:
            await self._bus.publish(event)
