"""Façade `SafeHttp` (cible server), contrat vds-core 1.12.0 : client HTTP sortant anti-SSRF."""

from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import BaseModel, Field

from ..errors import ContractError

__all__ = ["SafeHttp", "SafeHttpError", "SafeResponse"]


class SafeHttpError(ContractError):
    """Codes : net.blocked_address, net.scheme_refused, net.too_large, net.timeout,
    net.redirect_refused, net.http_status (+ net.dns_failed, net.unreachable)."""

    code = "net.error"


class SafeResponse(BaseModel):
    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: bytes
    final_url: str


class SafeHttp(Protocol):
    async def get(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout_s: float = 10.0,
        headers: Mapping[str, str] | None = None,
        accept: tuple[str, ...] = (),
        allow_host: Callable[[str], bool] | None = None,
    ) -> SafeResponse:
        """GET https public uniquement. Exige la permission `net.outbound`.

        `allow_host` est rappelé pour l'hôte de la requête puis de CHAQUE redirection ; faux =
        refus. Une réponse 304 est rendue (corps vide) si la requête portait `If-Modified-Since`
        ou `If-None-Match`."""
        ...
