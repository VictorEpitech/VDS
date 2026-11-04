"""Relance de l'application VDS d'une TV Android par ADB réseau (capacité `app.restart.android`).

Pas de scan : l'adresse est celle de `power.adb.host` de l'écran enregistré. Deux commandes shell
constantes (`am force-stop` puis `am start`), aucune sortie shell remontée. Le kiosque (Device Owner
ou kiosque doux) et l'appairage sont conservés : ni désinstallation, ni `pm clear`.
"""

import ipaddress

from vds_sdk.agent import AppRestartOutcome
from vds_sdk.agent.provision import ensure_lan_address

from .adb import AdbError, AdbProvisionTransport
from .provision_android import _VERSION_RE, CMD_LAUNCH, CMD_PACKAGES, PKG, _bad

U = "app.restart."
CMD_FORCE_STOP = f"am force-stop {PKG}"
CONNECT_TIMEOUT = 8.0
SHELL_TIMEOUT = 6.0  # 8 + 3 x 6 = 26 s au pire, sous le délai de commande (30 s)


def _fail(code: str) -> AppRestartOutcome:
    return AppRestartOutcome(ok=False, code=U + code)


class AndroidAppRestarter:
    def __init__(self, transport: AdbProvisionTransport) -> None:
        self._t = transport

    async def _launch(self, host: str, port: int) -> bool:
        """`am start`, retenté une fois (échec ou délai) après le force-stop."""
        for attempt in range(2):
            try:
                if not _bad(await self._t.shell(host, port, CMD_LAUNCH, SHELL_TIMEOUT)):
                    return True
            except (AdbError, TimeoutError):
                if attempt == 1:
                    raise
        return False

    async def restart(self, host: str, port: int) -> AppRestartOutcome:
        try:
            addr = ensure_lan_address(ipaddress.ip_address(host))
        except ValueError:
            return _fail("adb_unreachable")
        host = str(addr)

        async def refuse() -> None:  # invite RSA : échec rapide, aucune invite laissée sur la TV
            raise AdbError("power.adb.unauthorized")

        try:
            await self._t.probe(host, port, CONNECT_TIMEOUT, refuse)
            if not _VERSION_RE.search(await self._t.shell(host, port, CMD_PACKAGES, SHELL_TIMEOUT)):
                return _fail("not_installed")
            await self._t.shell(host, port, CMD_FORCE_STOP, SHELL_TIMEOUT)
            if not await self._launch(host, port):
                return _fail("launch_failed")
        except AdbError as e:
            denied = e.code == "power.adb.unauthorized"
            return _fail("adb_unauthorized" if denied else "adb_unreachable")
        return AppRestartOutcome(ok=True)
