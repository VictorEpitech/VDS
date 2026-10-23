"""En-têtes communs à tout le préfixe (aucune branche selon le mode)."""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = {
    "x-robots-tag": "noindex",
    "x-frame-options": "DENY",
    "cache-control": "private, no-store",
}


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for k, v in _HEADERS.items():
                    headers[k] = v
            await send(message)

        await self.app(scope, receive, send_wrapper)
