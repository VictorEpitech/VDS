"""Exécution de la commande fermée `apk.update` (vds-core 1.6).

Routage vers le module actif déclarant `update.<platform>` et implémentant `ScreenUpdater`.
L'APK n'est jamais une URL : même `ApkDownloader` que le provisioning (sha256 + taille vérifiés,
cache par sha256). Aucun scan, aucune adresse dans la commande : le module lit `power.adb.host`
de l'écran enregistré.

Garde-fous : un seul traitement par écran (verrou partagé avec `power.set`), au plus
`MAX_INSTALLS` mises à jour simultanées par site, un seul téléchargement par sha256.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from vds_sdk.agent import (
    ApkRef,
    ApkUpdateOutcome,
    ApkUpdateRequest,
    ScreenUpdater,
    StepReport,
)

from .provision import ApkDownloader, ApkError

log = logging.getLogger("vds.agent.apk_update")

UPDATE_TIMEOUT_S = 290.0  # sous le délai de commande serveur (300 s)
MAX_INSTALLS = 2


class _SharedSource:
    """`ApkSource` d'une mise à jour : sérialise le téléchargement par sha256 (le second écran
    relit le cache au lieu de retélécharger)."""

    def __init__(self, inner: ApkDownloader, locks: dict[str, asyncio.Lock]) -> None:
        self._inner = inner
        self._locks = locks

    async def fetch(self, apk: ApkRef) -> Path:
        lock = self._locks.setdefault(apk.sha256, asyncio.Lock())
        async with lock:
            return await self._inner.fetch(apk)


class ApkUpdateRunner:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        http_base: str,
        token: str,
        data_dir: Path,
        updaters: Callable[[str], ScreenUpdater | None],
        screen_lock: Callable[[str], asyncio.Lock],
        prepare: Callable[[str], bool],
        host_lock: Callable[[str], asyncio.Lock] | None = None,
        timeout_s: float = UPDATE_TIMEOUT_S,
        max_installs: int = MAX_INSTALLS,
    ) -> None:
        self._http = http
        self._base = http_base
        self._token = token
        self._data_dir = data_dir
        self._updaters = updaters
        self._screen_lock = screen_lock
        self._host_lock = host_lock  # verrou `host:port` ADB : deux écrans, un hôte, en série
        self._prepare = prepare  # False si l'écran n'est pas enregistré dans le plan
        self._timeout = timeout_s
        self._slots = asyncio.Semaphore(max_installs)
        self._fetch_locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _fail(code: str) -> tuple[str, str, dict[str, Any]]:
        return "failed", code, ApkUpdateOutcome(ok=False, code=code).model_dump(mode="json")

    async def run(
        self, screen_id: str, args: dict[str, Any]
    ) -> tuple[str, str | None, dict[str, Any]]:
        req = ApkUpdateRequest.model_validate({**args, "screen_id": screen_id})
        provider = self._updaters(req.platform)
        if provider is None:
            return self._fail("update.no_provider")
        if not self._prepare(screen_id):  # écran inconnu du plan : aucune connexion
            return self._fail("update.unknown_screen")
        lock = self._screen_lock(screen_id)
        if lock.locked():
            return self._fail(f"update.{req.platform}.busy")
        async with lock:
            hl = self._host_lock(screen_id) if self._host_lock else None
            async with hl if hl is not None else contextlib.nullcontext():
                async with self._slots:
                    return await self._execute(provider, req)

    async def _execute(
        self, provider: ScreenUpdater, req: ApkUpdateRequest
    ) -> tuple[str, str | None, dict[str, Any]]:
        async def report(step: StepReport) -> None:
            with contextlib.suppress(Exception):
                log.info(
                    "apk_update.step",
                    extra={"step": step.step, "status": step.status, "code": step.code},
                )

        source = _SharedSource(
            ApkDownloader(
                self._http,
                self._base,
                self._token,
                self._data_dir / "apk",
                req.platform,
                code_prefix="update",
            ),
            self._fetch_locks,
        )
        log.info(
            "apk_update.start",
            extra={"request_id": str(req.request_id), "version_code": req.apk.version_code},
        )
        try:
            async with asyncio.timeout(self._timeout):
                outcome = await provider.update(req, source, report)
        except TimeoutError:
            return self._fail("update.timeout")
        except ApkError as e:
            return self._fail(e.code)
        except Exception as e:  # noqa: BLE001 - jamais de sortie shell brute
            log.warning("apk_update.module_error", extra={"err": type(e).__name__})
            return self._fail("update.module_error")
        log.info(
            "apk_update.end",
            extra={"request_id": str(req.request_id), "ok": outcome.ok, "code": outcome.code},
        )
        data = outcome.model_dump(mode="json")
        return ("ok", None, data) if outcome.ok else ("failed", outcome.code, data)
