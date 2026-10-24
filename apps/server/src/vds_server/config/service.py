"""Service de configuration héritée : lecture résolue, écriture et suppression de surcharges."""

import os
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk.bus import Event, EventBus

from vds_server.config import resolver
from vds_server.config.resolver import LEVELS, ChainNode, Resolution, StoredOverride
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.core import audit
from vds_server.core.text import clean_reason
from vds_server.db.models import ConfigOverride, Organization, Screen, ScreenGroup, Site

_UNIQUE = "uq_config_override_scope_id_namespace_key"
_COLUMN = {
    "organization": "organization_id",
    "site": "site_id",
    "group": "group_id",
    "screen": "screen_id",
}


class ConfigError(Exception):
    """Erreur métier : `code` stable (traduit par l'admin), `status` HTTP suggéré."""

    def __init__(self, code: str, status: int, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params


async def upsert_override(
    session: AsyncSession,
    level: str,
    target_id: UUID,
    namespace: str,
    key: str,
    value: Any,
    actor_id: UUID | None,
) -> None:
    stmt = insert(ConfigOverride).values(
        {
            _COLUMN[level]: target_id,
            "namespace": namespace,
            "key": key,
            "value": value,
            "updated_by": actor_id,
        }
    )
    stmt = stmt.on_conflict_do_update(
        constraint=_UNIQUE,
        set_={"value": stmt.excluded.value, "updated_by": actor_id, "updated_at": func.now()},
    )
    await session.execute(stmt)


class ConfigService:
    def __init__(
        self,
        session: AsyncSession,
        schemas: ConfigSchemaProvider,
        bus: EventBus | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._s = session
        self._schemas = schemas
        self._bus = bus
        self._env = os.environ if env is None else env

    def _schema(self, namespace: str) -> dict[str, Any]:
        schema = self._schemas.get_schema(namespace)
        if schema is None:
            raise ConfigError("config.namespace_unknown", 404)
        return schema

    async def _get[T](self, model: type[T], pk: UUID) -> T:
        row = await self._s.get(model, pk)
        if row is None:
            raise ConfigError("config.target_unknown", 404)
        return row

    async def chain(self, level: str, target_id: UUID) -> list[ChainNode]:
        """Chaîne [organisation, site, groupe?, écran?] jusqu'à la cible."""
        if level not in LEVELS:
            raise ConfigError("config.level_invalid", 404)
        site_id: UUID | None = None
        group_id: UUID | None = None
        screen: Screen | None = None
        if level == "organization":
            org_id = target_id
        elif level == "site":
            site = await self._get(Site, target_id)
            site_id, org_id = site.id, site.organization_id
        elif level == "group":
            group = await self._get(ScreenGroup, target_id)
            group_id, site_id = group.id, group.site_id
            site = await self._get(Site, site_id)
            org_id = site.organization_id
        else:
            screen = await self._get(Screen, target_id)
            site_id, group_id = screen.site_id, screen.group_id
            site = await self._get(Site, site_id)
            org_id = site.organization_id
        org = await self._get(Organization, org_id)
        nodes = [ChainNode("organization", org.id, org.name)]
        if site_id is not None:
            site = await self._get(Site, site_id)
            nodes.append(ChainNode("site", site.id, site.name))
        if group_id is not None:
            group = await self._get(ScreenGroup, group_id)
            nodes.append(ChainNode("group", group.id, group.name))
        if screen is not None:
            nodes.append(ChainNode("screen", screen.id, screen.name))
        return nodes

    async def resolve(self, level: str, target_id: UUID, namespace: str) -> Resolution:
        schema = self._schema(namespace)
        chain = await self.chain(level, target_id)
        rows = (
            await self._s.execute(
                select(ConfigOverride).where(
                    ConfigOverride.namespace == namespace,
                    ConfigOverride.scope_id.in_([n.id for n in chain]),
                )
            )
        ).scalars()
        overrides = [
            StoredOverride(LEVELS[r.scope_level], r.scope_id, r.key, r.value) for r in rows
        ]
        return resolver.resolve(schema, chain, overrides, self._env, level)

    def _check_writable(self, schema: dict[str, Any], level: str, key: str) -> None:
        prop = schema.get("properties", {}).get(key)
        if prop is None:
            raise ConfigError("config.key_unknown", 404)
        if "x-vds-env" in prop:
            raise ConfigError("config.key_from_env", 409)
        if prop.get("readOnly") is True:
            raise ConfigError("config.read_only", 422)
        if level not in resolver.key_scopes(prop):
            raise ConfigError("config.scope_not_allowed", 422, level=level)

    async def set_override(
        self,
        level: str,
        target_id: UUID,
        namespace: str,
        key: str,
        value: Any,
        actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        schema = self._schema(namespace)
        await self.chain(level, target_id)
        self._check_writable(schema, level, key)
        errors = resolver.value_errors(schema, key, value)
        if errors:
            raise ConfigError("config.value_invalid", 422, paths=errors)
        await upsert_override(self._s, level, target_id, namespace, key, value, actor_id)
        self._audit("config.updated", actor_id, level, target_id, namespace, key)
        await self._audit_kiosk(actor_id, level, target_id, namespace, key, reason)
        await self._s.commit()
        await self._publish(namespace, key, level, target_id)

    def _audit(
        self,
        action: str,
        actor_id: UUID | None,
        level: str,
        target_id: UUID,
        namespace: str,
        key: str,
    ) -> None:
        # La valeur n'est jamais journalisée (peut être sensible) : clé et portée seulement.
        audit.record(
            self._s,
            actor_kind="user" if actor_id else "system",
            actor_id=str(actor_id) if actor_id else None,
            action=action,
            target_type=level,
            target_id=str(target_id),
            details={"namespace": namespace, "key": key},
        )

    async def _effective_kiosk(self, level: str, target_id: UUID) -> tuple[str, str]:
        """Mode effectif (après écriture) et niveau d'origine : écran > groupe > défaut locked."""
        nodes = await self.chain(level, target_id)
        rows = (
            await self._s.execute(
                select(ConfigOverride.scope_id, ConfigOverride.value).where(
                    ConfigOverride.namespace == "core",
                    ConfigOverride.key == "player.kiosk",
                    ConfigOverride.scope_id.in_([n.id for n in nodes]),
                )
            )
        ).all()
        stored = {sid: v for sid, v in rows}
        for node in reversed(nodes):
            if node.level in ("screen", "group") and stored.get(node.id) in ("locked", "unlocked"):
                return str(stored[node.id]), node.level
        return "locked", "default"

    async def _audit_kiosk(
        self,
        actor_id: UUID | None,
        level: str,
        target_id: UUID,
        namespace: str,
        key: str,
        reason: str | None,
        *,
        reset: bool = False,
    ) -> None:
        """Verrouillage/déverrouillage : `screen.kiosk.*` avec la valeur effective résolue."""
        if namespace != "core" or key != "player.kiosk":
            return
        await self._s.flush()
        mode, origin = await self._effective_kiosk(level, target_id)
        audit.record(
            self._s,
            actor_kind="user" if actor_id else "system",
            actor_id=str(actor_id) if actor_id else None,
            action="screen.kiosk.unlocked" if mode == "unlocked" else "screen.kiosk.locked",
            target_type=level,
            target_id=str(target_id),
            details={
                "scope_level": level,
                "reset": reset,
                "effective": mode,
                "origin": origin,
                "reason": clean_reason(reason),
            },
        )

    async def delete_override(
        self,
        level: str,
        target_id: UUID,
        namespace: str,
        key: str,
        actor_id: UUID | None = None,
        reason: str | None = None,
    ) -> bool:
        """Retour à l'héritage. Retourne False s'il n'y avait pas de surcharge (idempotent)."""
        schema = self._schema(namespace)
        await self.chain(level, target_id)
        if schema.get("properties", {}).get(key, {}).get("readOnly") is True:
            raise ConfigError("config.read_only", 422)
        if key not in schema.get("properties", {}) and not await self._stored(
            level, target_id, namespace, key
        ):
            raise ConfigError("config.key_unknown", 404)
        res = await self._s.execute(
            delete(ConfigOverride).where(
                ConfigOverride.scope_id == target_id,
                ConfigOverride.namespace == namespace,
                ConfigOverride.key == key,
            )
        )
        deleted = bool(res.rowcount)  # type: ignore[attr-defined]
        if deleted:
            self._audit("config.reset", actor_id, level, target_id, namespace, key)
            await self._audit_kiosk(actor_id, level, target_id, namespace, key, reason, reset=True)
        await self._s.commit()
        if deleted:
            await self._publish(namespace, key, level, target_id)
        return deleted

    async def _stored(self, level: str, target_id: UUID, namespace: str, key: str) -> bool:
        row = await self._s.execute(
            select(ConfigOverride.id).where(
                ConfigOverride.scope_id == target_id,
                ConfigOverride.namespace == namespace,
                ConfigOverride.key == key,
            )
        )
        return row.first() is not None

    async def _publish(self, namespace: str, key: str, level: str, scope_id: UUID) -> None:
        if self._bus is None:
            return
        await self._bus.publish(
            Event(
                name="config.changed",
                payload={
                    "namespace": namespace,
                    "keys": [key],
                    "scope_level": level,
                    "scope_id": str(scope_id),
                },
                source="core",
            )
        )
