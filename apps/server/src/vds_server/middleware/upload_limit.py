"""Plafond de taille des envois multipart, contrôlé avant que le corps soit lu et mis en tampon.

`Content-Length` obligatoire (411) et borné (413) ; sans confiance en l'en-tête, les octets
réellement reçus sont aussi comptés. La limite précise (fichier seul) reste dans le service.
"""

import json
from collections.abc import Awaitable, Callable

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vds_server.settings import Settings

OVERHEAD = 1024 * 1024  # champs de formulaire + délimiteurs multipart


class _TooLarge(Exception):
    pass


def _error(status: int, code: str) -> tuple[Message, Message]:
    body = json.dumps({"code": code, "params": {}}).encode()
    return (
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        },
        {"type": "http.response.body", "body": body},
    )


class UploadLimitMiddleware:
    def __init__(
        self, app: ASGIApp, settings: Settings, limit: Callable[[], Awaitable[int]]
    ) -> None:
        self.app = app
        self.path = f"{settings.prefix}/api/v1/media"
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return
        maximum = await self.limit() + OVERHEAD
        raw = Headers(scope=scope).get("content-length")
        if raw is None or not raw.isdigit():
            for m in _error(411, "media.length_required"):
                await send(m)
            return
        if int(raw) > maximum:
            for m in _error(413, "media.too_large"):
                await send(m)
            return
        received = 0

        async def counting() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise _TooLarge
            return message

        started = False

        async def tracking(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting, tracking)
        except _TooLarge:
            if not started:
                for m in _error(413, "media.too_large"):
                    await send(m)
