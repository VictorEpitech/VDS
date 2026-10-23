"""content.url : page web dans une iframe isolée. Ne dépend que de `vds_sdk`."""

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from vds_sdk import ContractError, ModuleContext
from vds_sdk.server.content import ContentType, PlayerItemSpec
from vds_sdk.server.media import MediaInfo


def _host(url: str) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError(url)
        parts.port  # noqa: B018 - lève ValueError si le port est invalide
        return parts.hostname.lower()
    except ValueError as exc:
        raise ContractError("content.url.invalid_url") from exc


def _origin(url: str) -> tuple[str, str, int] | None:
    """(schéma, hôte, port) normalisé ; None si l'URL n'est pas analysable."""
    try:
        parts = urlsplit(url.strip())
        if not parts.scheme or not parts.hostname:
            return None
        default = {"https": 443, "http": 80}.get(parts.scheme.lower())
        port = parts.port or default
        return (parts.scheme.lower(), parts.hostname.lower().rstrip("."), port or 0)
    except ValueError:
        return None


class ContentUrl(ContentType):
    def __init__(self) -> None:
        self._allowed: frozenset[str] = frozenset()
        self._own_origin: tuple[str, str, int] | None = None

    async def setup(self, ctx: ModuleContext) -> None:
        cfg = await ctx.config.resolve()
        # origine publique du VDS (indépendante de VDS_BASE_PATH) ; le player refuse aussi la sienne
        self._own_origin = _origin(os.environ.get("VDS_PUBLIC_ORIGIN", ""))
        self._allowed = frozenset(str(h).strip().lower() for h in cfg.get("allowed_hosts") or [])

    async def to_player(
        self, props: Mapping[str, Any], media: Mapping[UUID, MediaInfo]
    ) -> PlayerItemSpec:
        url = str(props.get("url") or "")
        host = _host(url)
        if self._own_origin is not None and _origin(url) == self._own_origin:
            raise ContractError("content.url.same_origin", host)
        if self._allowed and not any(host == a or host.endswith("." + a) for a in self._allowed):
            raise ContractError("content.url.host_not_allowed", host)
        return PlayerItemSpec(
            props={"url": url, "reload_interval_s": int(props.get("reload_interval_s") or 0)},
            assets=[],
        )
