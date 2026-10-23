"""ConfigReader d'un module : sa propre configuration résolue (organisation par défaut)."""

import os
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vds_sdk import ScopeRef

from vds_server.config import resolver
from vds_server.config.schemas import ConfigSchemaProvider
from vds_server.config.service import ConfigService
from vds_server.db.models import Organization


class ModuleConfigReader:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        schemas: ConfigSchemaProvider,
        namespace: str,
    ) -> None:
        self._maker = maker
        self._schemas = schemas
        self._ns = namespace

    async def resolve(self, scope: ScopeRef | None = None) -> dict[str, Any]:
        schema = self._schemas.get_schema(self._ns)
        if schema is None:
            return {}
        async with self._maker() as session:
            level = scope.level if scope else "organization"
            if scope and scope.id:
                target: UUID | None = UUID(scope.id)
            else:
                target = (await session.execute(select(Organization.id))).scalar_one_or_none()
            if target is None:  # instance neuve : valeurs par défaut du schéma
                res = resolver.resolve(schema, [], [], os.environ, level)
            else:
                res = await ConfigService(session, self._schemas).resolve(level, target, self._ns)
        # Le module propriétaire reçoit ses secrets en SecretStr (repr masqué).
        return {
            k: (SecretStr(v.secret_value or "") if v.secret else v.value)
            for k, v in res.values.items()
        }
