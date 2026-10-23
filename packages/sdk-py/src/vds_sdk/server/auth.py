"""Kind `auth-provider` : seule voie d'authentification du serveur."""

from abc import abstractmethod
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel

from ..errors import AccessDenied
from ..module import Module

__all__ = ["AccessDenied", "AuthProvider", "AuthRequest", "Identity", "ShellLink"]


class Identity(BaseModel):
    provider: str
    external_id: str
    email: str | None = None
    display_name: str
    role: Literal["admin", "editor"] | None = None  # None = rôle géré dans VDS (auth.local)


class AuthRequest(BaseModel):
    """Vue neutre de la requête (sans type FastAPI) transmise au provider."""

    method: str
    path: str
    headers: Mapping[str, str] = {}
    cookies: Mapping[str, str] = {}
    query: Mapping[str, str] = {}
    body: bytes = b""


class ShellLink(BaseModel):
    id: str
    label_key: str  # clé i18n
    url: str


class AuthProvider(Module):
    @abstractmethod
    async def authenticate(self, request: AuthRequest) -> Identity | None:
        """Identité ou None (non authentifié) ; lève AccessDenied(code) pour refuser."""

    @abstractmethod
    def login_url(self, return_to: str) -> str: ...

    @abstractmethod
    def logout_url(self) -> str: ...

    def shell_links(self) -> list[ShellLink]:
        return []
