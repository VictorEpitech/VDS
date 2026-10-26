"""Contrôle de même origine sur les écritures sous <base>/api/.

Le Bearer n'exempte que les routes à jeton d'appareil ou d'agent (jamais l'API admin à cookie).
"""

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
        # appairage : anonyme, sans cookie, appelé par le player (origine arbitraire) ; l'échange
        # par ticket est protégé par le ticket lui-même (usage unique, 30 min)
        self.exempt = {
            f"{settings.prefix}/api/v1/player/pairings",
            f"{settings.prefix}/api/v1/player/pair",
        }
        # routes à jeton (pas de cookie, pas de CSRF possible) : exemptées si Bearer présent
        self.bearer_exact = {f"{settings.prefix}/api/v1/player/heartbeat"}
        self.bearer_prefixes = (f"{settings.prefix}/api/v1/agent/",)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in _SAFE
            or not scope["path"].startswith(self.api_prefix)
            or scope["path"] in self.exempt
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        path = scope["path"]
        if headers.get("authorization", "").lower().startswith("bearer ") and (
            path in self.bearer_exact or path.startswith(self.bearer_prefixes)
        ):
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
