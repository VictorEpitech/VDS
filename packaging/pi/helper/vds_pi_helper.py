"""Assistant local de l'image Pi : relance de l'application (Chromium kiosque) et de l'appareil via systemd.

Écoute en boucle seulement (127.0.0.1), aucun port entrant. Seule l'origine du serveur VDS configurée
(`VDS_PI_ALLOWED_ORIGIN`) est servie : le navigateur impose l'en-tête `Origin`, donc une iframe `content.url`
ou une autre page ne peut pas déclencher d'action. Pas de capture d'écran en V1 (le port de débogage de
Chromium serait une surface d'attaque locale). Aucun secret, aucune sortie technique vers le public.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 47801
MIN_APP_GAP_S = 30.0
MIN_DEVICE_GAP_S = 300.0
UNIT_RE = "vds-kiosk.service"


class Policy:
    """Décision pure, testable sans réseau ni systemd."""

    def __init__(
        self,
        allowed_origin: str,
        allow_reboot: bool,
        run: Callable[[list[str]], None],
        clock: Callable[[], float] = time.monotonic,
        unit: str = UNIT_RE,
    ) -> None:
        self.allowed_origin = allowed_origin
        self.allow_reboot = allow_reboot
        self.run = run
        self.clock = clock
        self.unit = unit
        self._last: dict[str, float] = {}

    def handle(
        self, method: str, path: str, origin: str | None, content_type: str | None
    ) -> tuple[int, dict[str, str], dict[str, Any] | None]:
        if not self.allowed_origin or origin != self.allowed_origin:
            return 403, {}, None
        cors = {
            "Access-Control-Allow-Origin": self.allowed_origin,
            "Access-Control-Allow-Private-Network": "true",
            "Vary": "Origin",
            "Cache-Control": "no-store",
        }
        if method == "OPTIONS":
            cors |= {
                "Access-Control-Allow-Methods": "GET, POST",
                "Access-Control-Allow-Headers": "Content-Type",
            }
            return 204, cors, None
        if method == "GET" and path == "/v1/caps":
            return (
                200,
                cors,
                {
                    "helper": "vds-pi",
                    "restart_app": True,
                    "restart_device": self.allow_reboot,
                },
            )
        if method == "POST" and path in ("/v1/restart/app", "/v1/restart/device"):
            if (content_type or "").split(";")[0].strip().lower() != "application/json":
                return 415, cors, None  # une requête « simple » (sans prévalidation) est refusée
            scope = path.rsplit("/", 1)[1]
            if scope == "device" and not self.allow_reboot:
                return 403, cors, {"ok": False}
            gap = MIN_APP_GAP_S if scope == "app" else MIN_DEVICE_GAP_S
            now = self.clock()
            if now - self._last.get(scope, -1e9) < gap:
                return 429, cors, {"ok": False}
            self._last[scope] = now
            cmd = (
                ["systemctl", "reboot"]
                if scope == "device"
                else ["systemctl", "restart", self.unit]
            )
            self.run(cmd)
            return 202, cors, {"ok": True}
        return 404, cors, None


def _exec(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)  # noqa: S603


def _run(cmd: list[str]) -> None:
    # Liste fixe, jamais construite depuis la requête, sans shell ; différée pour que la réponse parte avant.
    threading.Timer(1.0, _exec, args=(cmd,)).start()


def make_handler(policy: Policy) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "vds-pi"
        sys_version = ""

        def _serve(self) -> None:
            status, headers, body = policy.handle(
                self.command,
                self.path.split("?", 1)[0],
                self.headers.get("Origin"),
                self.headers.get("Content-Type"),
            )
            raw = json.dumps(body).encode() if body is not None else b""
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            if raw:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_OPTIONS = _serve  # noqa: N815

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # aucune journalisation d'URL ni d'origine

    return Handler


def main() -> None:
    origin = os.environ.get("VDS_PI_ALLOWED_ORIGIN", "").rstrip("/")
    policy = Policy(origin, os.environ.get("VDS_PI_ALLOW_REBOOT") == "1", _run)
    ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(policy)).serve_forever()


if __name__ == "__main__":
    main()
