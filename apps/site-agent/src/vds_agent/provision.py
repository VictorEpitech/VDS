"""Exécution de la commande fermée `screen.provision` (vds-core 1.3).

L'agent ne connaît aucune plateforme : il route vers le module actif qui déclare
`provision.<platform>` et implémente `ScreenProvisioner`. L'APK n'est jamais désignée par une URL :
le chemin est construit ici à partir de `http_base` et du `version_code` entier.
Aucun port entrant ; le ticket d'appairage n'est jamais journalisé.
"""

import asyncio
import contextlib
import hashlib
import ipaddress
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from vds_sdk.agent import (
    ApkRef,
    ProvisionOutcome,
    ProvisionRequest,
    ScreenProvisioner,
    StepReport,
)
from vds_sdk.agent.provision import ensure_lan_address

log = logging.getLogger("vds.agent.provision")

PROVISION_TIMEOUT_S = 600.0
APK_CACHE_KEEP = 2
_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

Emit = Callable[[str, dict[str, Any]], Awaitable[None]]


class ApkError(Exception):
    """Erreur lisible : `code` = clé i18n `provision.<platform>.<raison>`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ApkDownloader:
    """`ApkSource` du runtime : téléchargement authentifié, sha256 et taille vérifiés."""

    def __init__(
        self, http: httpx.AsyncClient, http_base: str, token: str, cache_dir: Path, platform: str
    ) -> None:
        if not _PLATFORM_RE.match(platform):
            raise ValueError("provision.platform_invalid")
        self._http = http
        self._base = http_base
        self._token = token
        self._dir = cache_dir
        self._platform = platform

    def _err(self, reason: str) -> ApkError:
        return ApkError(f"provision.{self._platform}.{reason}")

    async def fetch(self, apk: ApkRef) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        final = self._dir / f"{apk.sha256}.apk"
        if final.is_file():
            if self._digest_ok(final, apk):
                os.utime(final)
                return final
            final.unlink(missing_ok=True)  # cache corrompu : on retélécharge
        url = f"{self._base}/api/v1/agent/apk/{self._platform}/{int(apk.version_code)}"
        tmp = self._dir / f".{apk.sha256}.part"
        try:
            sha = hashlib.sha256()
            size = 0
            async with self._http.stream(
                "GET", url, headers={"Authorization": f"Bearer {self._token}"}
            ) as resp:
                if resp.status_code != 200:
                    log.warning("provision.apk_http", extra={"status": resp.status_code})
                    raise self._err("apk_unavailable")
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(65536):
                        size += len(chunk)
                        if size > apk.size:  # jamais plus que la taille annoncée
                            raise self._err("apk_mismatch")
                        sha.update(chunk)
                        fh.write(chunk)
            if size != apk.size or sha.hexdigest() != apk.sha256:
                raise self._err("apk_mismatch")
            tmp.replace(final)
        except httpx.HTTPError as e:
            log.warning("provision.apk_fetch_failed", extra={"err": type(e).__name__})
            raise self._err("apk_unavailable") from e
        finally:
            tmp.unlink(missing_ok=True)
        self._prune(keep=final)
        return final

    @staticmethod
    def _digest_ok(path: Path, apk: ApkRef) -> bool:
        if path.stat().st_size != apk.size:
            return False
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest() == apk.sha256

    def _prune(self, keep: Path) -> None:
        files = sorted(self._dir.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in [f for f in files if f != keep][APK_CACHE_KEEP - 1 :]:
            old.unlink(missing_ok=True)


class ProvisionRunner:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        http_base: str,
        token: str,
        data_dir: Path,
        providers: Callable[[str], ScreenProvisioner | None],
        emit: Emit,
        local_addresses: Callable[[], list[str]],
        timeout_s: float = PROVISION_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._base = http_base
        self._token = token
        self._data_dir = data_dir
        self._providers = providers
        self._emit = emit
        self._local = local_addresses
        self._timeout = timeout_s
        self._busy: set[str] = set()  # verrou par IP cible

    def _own_address(self, addr: str) -> bool:
        ip = ipaddress.ip_address(addr)
        if ip.is_loopback:
            return True
        return addr in {str(ipaddress.ip_address(a)) for a in self._local()}

    @staticmethod
    def _fail(code: str) -> tuple[str, str, dict[str, Any]]:
        out = ProvisionOutcome(ok=False, kiosk="none", code=code)
        return "failed", code, out.model_dump(mode="json")

    async def run(
        self, screen_id: str, args: dict[str, Any]
    ) -> tuple[str, str | None, dict[str, Any]]:
        req = ProvisionRequest.model_validate({**args, "screen_id": screen_id})
        platform = req.platform
        try:  # re-validation indépendante du serveur
            ensure_lan_address(req.address)
        except ValueError:
            return self._fail("provision.address_not_lan")
        addr = str(req.address)
        if self._own_address(addr):
            return self._fail("provision.address_is_agent")
        provider = self._providers(platform)
        if provider is None:
            return self._fail("provision.no_provider")
        if addr in self._busy:
            return self._fail("provision.busy")
        self._busy.add(addr)
        try:
            return await self._execute(provider, req)
        finally:
            self._busy.discard(addr)

    async def _execute(
        self, provider: ScreenProvisioner, req: ProvisionRequest
    ) -> tuple[str, str | None, dict[str, Any]]:
        last: dict[str, StepReport] = {}

        async def report(step: StepReport) -> None:
            last[step.step] = step
            payload = {
                "provision_id": str(req.provision_id),
                "screen_id": str(req.screen_id),
                **step.model_dump(mode="json"),
            }
            with contextlib.suppress(Exception):  # la progression ne doit pas tuer l'installation
                await self._emit("screen.provision_progressed", payload)

        source = ApkDownloader(
            self._http, self._base, self._token, self._data_dir / "apk", req.platform
        )
        log.info(
            "provision.start",
            extra={"provision_id": str(req.provision_id), "platform": req.platform},
        )
        try:
            async with asyncio.timeout(self._timeout):
                outcome = await provider.provision(req, source, report)
        except TimeoutError:
            return self._fail("provision.timeout")
        except ApkError as e:
            return self._fail(e.code)
        except Exception as e:  # noqa: BLE001 - jamais de détail brut (ticket, sortie shell)
            log.warning("provision.module_error", extra={"err": type(e).__name__})
            return self._fail("provision.module_error")
        log.info(
            "provision.end",
            extra={"provision_id": str(req.provision_id), "ok": outcome.ok, "code": outcome.code},
        )
        data = outcome.model_dump(mode="json")
        return ("ok", None, data) if outcome.ok else ("failed", outcome.code, data)
