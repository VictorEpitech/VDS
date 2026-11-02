"""Mise à jour de l'APK player d'une TV Android par ADB réseau (capacité `update.android`).

Pas de scan : l'adresse est celle de `power.adb.host` de l'écran, fournie par le module. Commandes
shell constantes. Jamais de désinstallation (perdrait Device Owner et appairage) ; jamais de
rétrogradation. `install -r` du même paquet conserve Device Owner, appairage et activité d'accueil.
"""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from pathlib import Path

from vds_sdk.agent import ApkSource, ApkUpdateOutcome, ApkUpdateRequest, StepReport
from vds_sdk.agent.provision import ProvisionStepId, StepStatus, ensure_lan_address

from .adb import AdbError, AdbProvisionTransport
from .provision_android import (
    _VERSION_RE,
    CMD_LAUNCH,
    CMD_PACKAGES,
    _bad,
    _check_file,
    install_package,
)

U = "update.android."
CONNECT_TIMEOUT = 10.0
SHELL_TIMEOUT = 15.0
INSTALL_TIMEOUT = 200.0  # sous le délai de commande (300 s) avec téléchargement et relance

Report = Callable[[StepReport], Awaitable[None]]


class _Fail(Exception):  # noqa: N818 - fin anticipée
    def __init__(self, step: ProvisionStepId, code: str, **extra: int | None) -> None:
        super().__init__(code)
        self.step = step
        self.code = code
        self.extra = extra


def _adb_code(e: AdbError) -> str:
    return U + ("adb_unauthorized" if e.code == "power.adb.unauthorized" else "adb_unreachable")


class AndroidUpdater:
    platforms = frozenset({"android"})

    def __init__(self, transport: AdbProvisionTransport) -> None:
        self._t = transport

    async def update(
        self,
        req: ApkUpdateRequest,
        host: str,
        port: int,
        apk: ApkSource,
        report: Report,
    ) -> ApkUpdateOutcome:
        async def rep(
            step: ProvisionStepId, status: StepStatus, code: str | None = None, **d: int | bool
        ) -> None:
            await report(StepReport(step=step, status=status, code=code, detail=d))

        previous: int | None = None
        current: ProvisionStepId = "connect"
        try:
            try:
                addr = ensure_lan_address(ipaddress.ip_address(host))
            except ValueError as e:
                raise _Fail("connect", U + "address_not_lan") from e
            host = str(addr)
            await self._connect(host, port, rep)
            current = "inspect"
            await rep("inspect", "running")
            previous = await self._version(host, port)
            if previous is None:
                raise _Fail("inspect", U + "not_installed")
            target = req.apk.version_code
            await rep("inspect", "ok", None, installed_version_code=previous)
            if previous > target:
                raise _Fail("inspect", U + "downgrade_refused", previous=previous)
            if previous == target:
                for s in ("download", "verify", "install", "launch"):
                    await rep(s, "skipped", U + "already_up_to_date")  # type: ignore[arg-type]
                return ApkUpdateOutcome(
                    ok=True,
                    previous_version_code=previous,
                    installed_version_code=previous,
                    code=U + "already_up_to_date",
                )
            current = "download"
            await rep("download", "running")
            try:
                path = await apk.fetch(req.apk)
            except Exception as e:  # noqa: BLE001 - aucun détail réseau dans le rapport
                bad = str(getattr(e, "code", "")).endswith("apk_mismatch")
                raise _Fail(
                    "download" if not bad else "verify",
                    U + ("verify_failed" if bad else "apk_unavailable"),
                ) from e
            await rep("download", "ok")
            current = "verify"
            await rep("verify", "running")
            if not await asyncio.to_thread(_check_file, path, req.apk.sha256, req.apk.size):
                raise _Fail("verify", U + "verify_failed")
            await rep("verify", "ok")
            current = "install"
            await self._install(host, port, path, req, rep)
            now = await self._version(host, port)  # état réel après installation
            if now != target:
                raise _Fail("install", U + "install_failed")
            current = "launch"
            await rep("launch", "running")
            if _bad(await self._t.shell(host, port, CMD_LAUNCH, SHELL_TIMEOUT)):
                raise _Fail("launch", U + "launch_failed", installed=now)
            await rep("launch", "ok")
        except _Fail as e:
            await rep(e.step, "failed", e.code)
            return ApkUpdateOutcome(
                ok=False,
                previous_version_code=previous,
                installed_version_code=e.extra.get("installed"),
                code=e.code,
            )
        except AdbError as e:
            code = U + "install_failed" if current == "install" else _adb_code(e)
            await rep(current, "failed", code)
            return ApkUpdateOutcome(ok=False, previous_version_code=previous, code=code)
        return ApkUpdateOutcome(
            ok=True, previous_version_code=previous, installed_version_code=target
        )

    async def _connect(self, host: str, port: int, rep: Callable[..., Awaitable[None]]) -> None:
        await rep("connect", "running")

        async def refuse() -> None:  # invite RSA affichée : échec rapide, rien n'attend sur la TV
            raise AdbError("power.adb.unauthorized")

        try:
            await self._t.probe(host, port, CONNECT_TIMEOUT, refuse)
        except AdbError as e:
            raise _Fail("connect", _adb_code(e)) from e
        await rep("connect", "ok")

    async def _version(self, host: str, port: int) -> int | None:
        try:
            out = await self._t.shell(host, port, CMD_PACKAGES, SHELL_TIMEOUT)
        except AdbError as e:
            raise _Fail("inspect", _adb_code(e)) from e
        m = _VERSION_RE.search(out)
        return int(m.group(1)) if m else None

    async def _install(
        self,
        host: str,
        port: int,
        path: Path,
        req: ApkUpdateRequest,
        rep: Callable[..., Awaitable[None]],
    ) -> None:
        await rep("install", "running")
        result = await install_package(self._t, host, port, path, req.apk.size, INSTALL_TIMEOUT)
        if result == "incompatible":
            raise _Fail("install", U + "install_incompatible")
        if result == "failed":
            raise _Fail("install", U + "install_failed")
        await rep("install", "ok", None, version_code=req.apk.version_code)
