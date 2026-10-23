"""Cache média de site (lot 3 T9) : manifeste, clé HMAC du service LAN, lecture par l'agent."""

import hashlib
import hmac
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk.wire import CacheInfo

from vds_server.agents.auth import AgentPrincipal
from vds_server.db.models import Media, MediaVariant, Screen
from vds_server.db.models_power import SiteAgent
from vds_server.player.schedule import screen_media_ids

if TYPE_CHECKING:
    from vds_server.core.runtime import Runtime


def hkdf_sha256(secret: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF (RFC 5869), sel vide : pas de dépendance supplémentaire côté serveur."""
    prk = hmac.new(b"\x00" * 32, secret, hashlib.sha256).digest()
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def cache_key(secret: str, agent_id: UUID, rotated_at: datetime | None) -> tuple[str, str]:
    """(key_id, clé hex) propres à l'agent ; la rotation du jeton renouvelle la clé."""
    rotation = rotated_at.isoformat() if rotated_at else "0"
    info = b"vds-cache" + agent_id.bytes + rotation.encode()
    key = hkdf_sha256(secret.encode(), info)
    key_id = hashlib.sha256(info).hexdigest()[:8]
    return key_id, key.hex()


def cache_info(secret: str | None, row: SiteAgent) -> CacheInfo:
    """`welcome.cache` : sans `VDS_SECRET_KEY`, pas de clé, donc pas de service LAN signé."""
    if not secret:
        return CacheInfo()
    key_id, key = cache_key(secret, row.id, row.rotated_at)
    return CacheInfo(enabled=True, key_id=key_id, key=key)


async def site_media_ids(session: AsyncSession, site_id: UUID) -> set[UUID]:
    ids = (
        await session.execute(
            select(Screen.id).where(Screen.site_id == site_id, Screen.archived_at.is_(None))
        )
    ).scalars()
    out: set[UUID] = set()
    for sid in list(ids):
        out |= await screen_media_ids(session, sid)
    return out


async def build_manifest(session: AsyncSession, site_id: UUID) -> dict[str, Any]:
    media = await site_media_ids(session, site_id)
    items: list[dict[str, Any]] = []
    if media:
        q = (
            select(MediaVariant)
            .join(Media, Media.id == MediaVariant.media_id)
            .where(
                MediaVariant.media_id.in_(media),
                Media.status == "ready",
                MediaVariant.sha256.is_not(None),
            )
            .order_by(MediaVariant.media_id, MediaVariant.name)
        )
        for v in (await session.execute(q)).scalars():
            items.append(
                {
                    "media_id": str(v.media_id),
                    "variant": v.name,
                    "sha256": v.sha256,
                    "bytes": v.bytes,
                    "path": f"media/{v.media_id}/{v.name}",
                }
            )
    body: dict[str, Any] = {"items": items}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["revision"] = hashlib.sha256(raw).hexdigest()[:16]
    return body


def build_router(rt: "Runtime") -> APIRouter:
    router = APIRouter(tags=["agent-cache"])

    @router.get("/api/v1/agent/cache/manifest")
    async def manifest(
        request: Request,
        principal: AgentPrincipal = Depends(rt.agent_auth),  # noqa: B008
    ) -> Response:
        async with rt.maker() as s:
            body = await build_manifest(s, principal.site_id)
        etag = f'"{body["revision"]}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-store"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(json.dumps(body), media_type="application/json", headers=headers)

    return router
