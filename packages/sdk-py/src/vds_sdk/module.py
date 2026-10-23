"""Classe de base `Module` et contexte fourni par le noyau."""

import logging
from abc import ABC
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel

from .bus import ScopedBus

if TYPE_CHECKING:
    from .server.media import MediaFacade

ScopeLevel = Literal["organization", "site", "group", "screen"]


class ScopeRef(BaseModel):
    level: ScopeLevel
    id: str | None = None  # None pour l'organisation


class Health(BaseModel):
    status: Literal["ok", "degraded", "down"] = "ok"
    code: str | None = None  # code stable, traduit côté admin


class ConfigReader(Protocol):
    async def resolve(self, scope: ScopeRef | None = None) -> dict[str, Any]: ...


class CoreApi(Protocol):
    """Façade du noyau filtrée par permissions (méthodes ajoutées par les lots)."""

    media: "MediaFacade"  # permissions media.read / media.write (aucune nouvelle)


class ModuleContext(Protocol):
    module_id: str
    bus: ScopedBus
    config: ConfigReader
    core: CoreApi
    log: logging.Logger

    def t(self, key: str, locale: str, **params: Any) -> str: ...


class Module(ABC):  # noqa: B024 - cycle de vie à surcharger, défauts volontaires
    async def setup(self, ctx: ModuleContext) -> None:
        return None

    async def teardown(self) -> None:
        return None

    async def health(self) -> Health:
        return Health()

    def routes(self) -> Any | None:
        """`APIRouter` FastAPI (permission http.routes) ; Any : le SDK n'importe pas FastAPI."""
        return None
