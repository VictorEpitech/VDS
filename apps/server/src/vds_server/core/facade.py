"""Façade `CoreApi` fournie aux modules, filtrée par permissions du manifeste."""

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, ModuleManifest, PermissionDenied
from vds_sdk.server import Identity

from vds_server.core import audit, users
from vds_server.db.models import UserAccount
from vds_server.db.models_power import Incident
from vds_server.security import cookies
from vds_server.security.client_ip import client_ip
from vds_server.security.return_to import safe_return_to
from vds_server.supervision.incidents import view as incident_view

if TYPE_CHECKING:
    from vds_server.core.runtime import Runtime


class _Db:
    def __init__(self, rt: "Runtime") -> None:
        self._rt = rt

    def session(self) -> AsyncSession:
        """Session sur la base du serveur ; le module n'écrit que dans ses tables m_<id>_*."""
        return self._rt.maker()


class _Http:
    def __init__(self, rt: "Runtime") -> None:
        self._s = rt.settings

    def url(self, path: str = "/") -> str:
        return self._s.url(path)

    def cookie_path(self) -> str:
        return cookies.cookie_path(self._s)

    def set_cookie(
        self, response: Response, key: str, value: str, *, max_age: int | None = None
    ) -> None:
        cookies.set_cookie(response, self._s, key, value, max_age=max_age)

    def delete_cookie(self, response: Response, key: str) -> None:
        cookies.delete_cookie(response, self._s, key)

    def safe_return_to(self, value: str | None) -> str:
        return safe_return_to(value, self._s)

    def client_ip(self, request: Request) -> str:
        return client_ip(request)


class _Auth:
    """Dépendances FastAPI pour les routes d'un module (principal, rôle minimal)."""

    def __init__(self, rt: "Runtime") -> None:
        self.principal = rt.pipeline.principal
        self.require = rt.pipeline.require


class _Users:
    def __init__(self, rt: "Runtime", module_id: str, permissions: frozenset[str]) -> None:
        self._rt = rt
        self._module_id = module_id
        self._permissions = permissions
        self._tasks: set[asyncio.Task[None]] = set()

    async def list(self, role: users.Role | None = None) -> list[dict[str, Any]]:
        """Comptes actifs ayant un e-mail (permission users.read) : e-mail, locale, rôle."""
        if "users.read" not in self._permissions:
            raise PermissionDenied(detail=f"users.read by {self._module_id}")
        stmt = select(UserAccount).where(UserAccount.is_active, UserAccount.email.is_not(None))
        if role is not None:
            stmt = stmt.where(UserAccount.role == role)
        async with self._rt.maker() as session:
            rows = (await session.execute(stmt.order_by(UserAccount.email))).scalars().all()
        return [
            {
                "email": str(r.email),
                "locale": r.locale or self._rt.settings.default_locale,
                "role": r.role,
                "display_name": r.display_name,
            }
            for r in rows
        ]

    async def provision(self, session: AsyncSession, identity: Identity, role: users.Role) -> UUID:
        """Crée le compte noyau dans la transaction du module (le module commit).
        `user.created` est publié après le commit."""
        if "users.provision" not in self._permissions:
            raise PermissionDenied(detail=f"users.provision by {self._module_id}")
        if identity.provider != self._module_id:
            raise PermissionDenied(detail="users.provision foreign provider")
        row, created = await users.upsert_identity(session, identity, role_if_new=role)
        if created:
            event_ = Event(name="user.created", payload={"id": str(row.id)}, source="core")

            def after_commit(_: Any) -> None:
                task = asyncio.get_running_loop().create_task(self._rt.bus.publish(event_))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

            event.listen(session.sync_session, "after_commit", after_commit, once=True)
        return row.id


class _Audit:
    def __init__(self, module_id: str) -> None:
        self._module_id = module_id

    def record(
        self,
        session: AsyncSession,
        *,
        action: str,
        actor_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        audit.record(
            session,
            actor_kind="user" if actor_id else "module",
            actor_id=actor_id or self._module_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details={**(details or {}), "module": self._module_id},
        )


class _Incidents:
    def __init__(self, rt: "Runtime") -> None:
        self._rt = rt

    async def get(self, incident_id: UUID) -> dict[str, Any] | None:
        """Incident avec noms de sujet, site et fuseau (permission incidents.read)."""
        async with self._rt.maker() as session:
            row = await session.get(Incident, incident_id)
            return await incident_view(session, row) if row is not None else None


class ModuleCore:
    def __init__(self, rt: "Runtime", manifest: ModuleManifest) -> None:
        self._rt = rt
        self._m = manifest
        self.audit = _Audit(manifest.id)

    def _need(self, permission: str) -> None:
        if permission not in self._m.permissions:
            raise PermissionDenied(detail=f"{permission} by {self._m.id}")

    @property
    def db(self) -> _Db:
        self._need("db.own-tables")
        return _Db(self._rt)

    @property
    def http(self) -> _Http:
        self._need("http.routes")
        return _Http(self._rt)

    @property
    def auth(self) -> _Auth:
        self._need("http.routes")
        return _Auth(self._rt)

    @property
    def users(self) -> _Users:
        if not {"users.provision", "users.read"} & set(self._m.permissions):
            raise PermissionDenied(detail=f"users.* by {self._m.id}")
        return _Users(self._rt, self._m.id, frozenset(self._m.permissions))

    @property
    def incidents(self) -> _Incidents:
        self._need("incidents.read")
        return _Incidents(self._rt)
