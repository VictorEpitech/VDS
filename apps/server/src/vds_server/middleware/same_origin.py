"""Contrôle de même origine sur les écritures sous <base>/api/ sans Bearer."""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from vds_server.settings import Settings

_SAFE = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _request_origin(scope: Scope, headers: Headers, settings: Settings) -> str:
    if settings.public_origin:
        return settings.public_origin.rstrip("/")
    host = headers.get("host", "")
    return f"{scope.get('scheme', 'http')}://{host}"


class SameOriginMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.api_prefix = f"{settings.prefix}/api/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in _SAFE
            or not scope["path"].startswith(self.api_prefix)
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if headers.get("authorization", "").lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return
        site = headers.get("sec-fetch-site")
        origin = headers.get("origin")
        ok = site == "same-origin" or (
            origin is not None
            and origin.rstrip("/") == _request_origin(scope, headers, self.settings)
        )
        if ok:
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"error": {"code": "errors.origin_mismatch"}}, status_code=403)
        await response(scope, receive, send)
