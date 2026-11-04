"""Exécution de la commande fermée `app.restart` (vds-core 1.14).

Routage vers le module actif déclarant `app.restart.<platform>` et implémentant `AppRestarter`.
Aucune adresse dans la commande : le module lit `power.adb.host` de l'écran enregistré.
Garde-fous : verrou d'écran et verrou d'hôte partagés avec `power.set` et `apk.update` (refus
`busy` si l'un tourne), au plus une relance par `MIN_GAP_S` et par appareil. Jamais de sortie shell.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError
from vds_sdk.agent import AppRestarter, AppRestartOutcome, AppRestartRequest

log = logging.getLogger("vds.agent.app_restart")

RESTART_TIMEOUT_S = 28.0  # sous le délai de commande serveur (30 s)
MIN_GAP_S = 30.0  # anti-rafale local (le serveur applique aussi 30 s : restart_app_too_soon)


class AppRestartRunner:
    def __init__(
        self,
        *,
        restarters: Callable[[str], AppRestarter | None],
        screen_lock: Callable[[str], asyncio.Lock],
        prepare: Callable[[str], bool],
        host_lock: Callable[[str], asyncio.Lock] | None = None,
        timeout_s: float = RESTART_TIMEOUT_S,
        min_gap_s: float = MIN_GAP_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._restarters = restarters
        self._screen_lock = screen_lock
        self._host_lock = host_lock
        self._prepare = prepare  # False si l'écran n'est pas enregistré dans le plan
        self._timeout = timeout_s
        self._gap = min_gap_s
        self._clock = clock
        self._last: dict[str, float] = {}

    @staticmethod
    def _fail(code: str) -> tuple[str, str, dict[str, Any]]:
        return "failed", code, AppRestartOutcome(ok=False, code=code).model_dump(mode="json")

    async def run(
        self, screen_id: str, args: dict[str, Any]
    ) -> tuple[str, str | None, dict[str, Any]]:
        try:
            req = AppRestartRequest.model_validate({**args, "screen_id": screen_id})
        except ValidationError:
            return self._fail("app.restart.unsupported")
        provider = self._restarters(req.platform)
        if provider is None:
            return self._fail("app.restart.unsupported")
        if not self._prepare(screen_id):  # écran inconnu du plan : aucune connexion
            return self._fail("app.restart.unsupported")
        lock = self._screen_lock(screen_id)
        if lock.locked():
            return self._fail("app.restart.busy")
        async with lock:
            hl = self._host_lock(screen_id) if self._host_lock else None
            if hl is not None and hl.locked():  # mise à jour APK ou autre écran sur le même hôte
                return self._fail("app.restart.busy")
            async with hl if hl is not None else contextlib.nullcontext():
                now = self._clock()
                last = self._last.get(screen_id)
                if last is not None and now - last < self._gap:
                    return self._fail("app.restart.busy")  # anti-rafale
                self._last[screen_id] = now
                return await self._execute(provider, req)

    async def _execute(
        self, provider: AppRestarter, req: AppRestartRequest
    ) -> tuple[str, str | None, dict[str, Any]]:
        log.info("app_restart.start", extra={"request_id": str(req.request_id)})
        try:
            async with asyncio.timeout(self._timeout):
                outcome = await provider.restart_app(req)
        except TimeoutError:
            return self._fail("app.restart.adb_unreachable")
        except Exception as e:  # noqa: BLE001 - jamais de sortie shell brute
            log.warning("app_restart.module_error", extra={"err": type(e).__name__})
            return self._fail("app.restart.launch_failed")
        log.info("app_restart.end", extra={"ok": outcome.ok, "code": outcome.code})
        data = outcome.model_dump(mode="json")
        return ("ok", None, data) if outcome.ok else ("failed", outcome.code, data)
