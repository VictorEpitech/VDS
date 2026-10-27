"""Provisioning d'une TV Android par ADB réseau (capacité `provision.android`).

Toutes les commandes shell sont des constantes ; seuls des arguments validés (adresse, entiers)
ou échappés strictement (`shlex.quote`) y entrent. Aucun rapport ne contient le ticket ni une
sortie shell brute.
"""

import asyncio
import hashlib
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path

from vds_sdk.agent import (
    ApkSource,
    ProvisionOutcome,
    ProvisionRequest,
    StepReport,
)
from vds_sdk.agent.provision import ProvisionStepId, StepStatus

from .adb import AdbError, AdbProvisionTransport

PKG = "app.vds.player"
ADMIN = f"{PKG}/.AdminReceiver"
PROVISION_ACTIVITY = f"{PKG}/.ProvisionActivity"
MAIN_ACTIVITY = f"{PKG}/.MainActivity"
P = "provision.android."

STEPS: tuple[ProvisionStepId, ...] = (
    "connect",
    "authorize",
    "inspect",
    "download",
    "verify",
    "install",
    "device_owner",
    "soft_kiosk",
    "configure",
    "inject",
    "launch",
)
ALWAYS = frozenset({"connect", "authorize", "inspect"})
MIN_SDK = 24
AUTHORIZE_TIMEOUT = 90.0
SHELL_TIMEOUT = 15.0
INSTALL_TIMEOUT = 300.0

CMD_SDK = "getprop ro.build.version.sdk"
CMD_MODEL = "getprop ro.product.model"
CMD_ACCOUNTS = "dumpsys account"
CMD_USERS = "pm list users"
CMD_PACKAGES = f"pm list packages --show-versioncode {PKG}"
CMD_OWNERS = "dpm list-owners"
CMD_SET_OWNER = f"dpm set-device-owner {ADMIN}"
CMD_HOME = f"cmd package set-home-activity {MAIN_ACTIVITY}"
CMD_OVERLAY = f"appops set {PKG} SYSTEM_ALERT_WINDOW allow"
CMD_AWAKE = "settings put secure attentive_timeout -1"
CMD_LAUNCH = f"am start -n {MAIN_ACTIVITY}"

_ACCOUNTS_RE = re.compile(r"^\s*Accounts:\s*(\d+)", re.M)
_VERSION_RE = re.compile(rf"package:{re.escape(PKG)}\s+versionCode:(\d+)")
_FAILURE_RE = re.compile(r"Failure \[([A-Z_0-9]{1,64})")
_INCOMPATIBLE = ("INSTALL_FAILED_UPDATE_INCOMPATIBLE", "INSTALL_FAILED_SHARED_USER_INCOMPATIBLE")

Report = Callable[[StepReport], Awaitable[None]]
Rep = Callable[..., Awaitable[None]]


def _check_file(path: Path, sha256: str, size: int) -> bool:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
            n += len(block)
    if h.hexdigest() == sha256 and n == size:
        return True
    path.unlink(missing_ok=True)  # fichier suspect retiré du cache
    return False


class _Stop(Exception):  # noqa: N818 - fin anticipée d'une étape bloquante
    def __init__(self, step: ProvisionStepId, code: str) -> None:
        super().__init__(code)
        self.step = step
        self.code = code


def _code(adb_code: str) -> str:
    return P + adb_code.removeprefix("power.adb.")


def _bad(out: str) -> bool:
    return any(t in out for t in ("Exception", "Error", "Unknown", "not found", "Failure"))


class AndroidProvisioner:
    platforms = frozenset({"android"})

    def __init__(self, transport: AdbProvisionTransport) -> None:
        self._t = transport

    async def _sh(self, req: ProvisionRequest, cmd: str, timeout: float = SHELL_TIMEOUT) -> str:
        return await self._t.shell(str(req.address), req.port, cmd, timeout)

    async def provision(
        self, req: ProvisionRequest, apk: ApkSource, report: Report
    ) -> ProvisionOutcome:
        start = STEPS.index(req.from_step) if req.from_step else 0

        def due(step: ProvisionStepId) -> bool:
            return step in ALWAYS or STEPS.index(step) >= start

        async def rep(
            step: ProvisionStepId,
            status: StepStatus,
            code: str | None = None,
            **detail: str | int | bool,
        ) -> None:
            await report(StepReport(step=step, status=status, code=code, detail=detail))

        current: ProvisionStepId = "connect"
        try:
            path: Path | None = None
            await self._connect(req, rep)
            current = "inspect"
            await rep("inspect", "running")
            info = await self._inspect(req, rep)
            installed = info["version"]
            if installed == req.apk.version_code:
                for s in ("download", "verify", "install"):
                    if due(s):  # type: ignore[arg-type]
                        await rep(s, "skipped", P + "already_installed")
            else:
                if due("download"):
                    current = "download"
                    path = await self._download(req, apk, rep)
                    current = "verify"
                    await self._verify(req, path, rep)
                if due("install"):
                    current = "install"
                    installed = await self._install(req, path, rep)
            owner = False
            if due("device_owner"):
                current = "device_owner"
                owner = await self._device_owner(req, info, rep)
            else:
                owner = bool(info["ours"])
            soft = False
            if due("soft_kiosk"):
                current = "soft_kiosk"
                soft = await self._soft_kiosk(req, owner, rep)
            if due("configure"):
                current = "configure"
                await self._configure(req, rep)
            current = "inject"
            await self._inject(req, rep)
            current = "launch"
            await self._launch(req, rep)
        except _Stop as e:
            await rep(e.step, "failed", e.code)
            return ProvisionOutcome(ok=False, kiosk="none", code=e.code)
        except AdbError as e:
            await rep(current, "failed", _code(e.code))
            return ProvisionOutcome(ok=False, kiosk="none", code=_code(e.code))
        kiosk = "device_owner" if owner else "soft" if soft else "none"
        code = P + "no_kiosk" if kiosk == "none" else None
        return ProvisionOutcome(
            ok=True, kiosk=kiosk, installed_version_code=int(installed), code=code
        )

    async def _connect(self, req: ProvisionRequest, rep: Rep) -> None:
        await rep("connect", "running")
        state = {"pending": False}

        async def on_pending() -> None:
            state["pending"] = True
            await rep("connect", "ok")
            await rep("authorize", "waiting_user", P + "accept_on_tv")

        try:
            await self._t.probe(str(req.address), req.port, AUTHORIZE_TIMEOUT, on_pending)
        except AdbError as e:
            step: ProvisionStepId = "authorize" if state["pending"] else "connect"
            code = P + "unreachable" if e.code == "power.adb.timeout" else _code(e.code)
            raise _Stop(step, code) from e
        if not state["pending"]:
            await rep("connect", "ok")
        await rep("authorize", "ok")

    async def _inspect(self, req: ProvisionRequest, rep: Rep) -> dict[str, int | bool]:
        sdk_raw = (await self._sh(req, CMD_SDK)).strip()
        sdk = int(sdk_raw) if sdk_raw.isdigit() else 0
        model = (await self._sh(req, CMD_MODEL)).strip()[:48]
        if sdk < MIN_SDK:
            raise _Stop("inspect", P + "sdk_too_old")
        accounts = sum(int(n) for n in _ACCOUNTS_RE.findall(await self._sh(req, CMD_ACCOUNTS)))
        users = (await self._sh(req, CMD_USERS)).count("UserInfo{")
        m = _VERSION_RE.search(await self._sh(req, CMD_PACKAGES))
        owners = await self._sh(req, CMD_OWNERS)
        other = bool(re.search(r"(?i)device.?owner|admin=", owners)) and PKG not in owners
        info: dict[str, int | bool] = {
            "sdk": sdk,
            "accounts": accounts,
            "users": users,
            "version": int(m.group(1)) if m else 0,
            "ours": PKG in owners,
            "other": other,
        }
        await rep(
            "inspect",
            "ok",
            None,
            sdk=sdk,
            model=model,
            accounts=accounts,
            users=users,
            installed_version_code=int(info["version"]),
        )
        return info

    async def _download(self, req: ProvisionRequest, apk: ApkSource, rep: Rep) -> Path:
        await rep("download", "running")
        try:
            path = await apk.fetch(req.apk)
        except Exception as e:  # noqa: BLE001 - jamais de détail réseau brut dans le rapport
            raise _Stop("download", P + "apk_unavailable") from e
        await rep("download", "ok")
        return path

    async def _verify(self, req: ProvisionRequest, path: Path, rep: Rep) -> None:
        await rep("verify", "running")
        if not await asyncio.to_thread(_check_file, path, req.apk.sha256, req.apk.size):
            raise _Stop("verify", P + "apk_mismatch")
        await rep("verify", "ok")

    async def _install(self, req: ProvisionRequest, path: Path | None, rep: Rep) -> int:
        if path is None:  # reprise après install : la version est connue par `inspect`
            await rep("install", "skipped", P + "already_installed")
            return req.apk.version_code
        await rep("install", "running")
        cmd = f"cmd package install -r -S {int(req.apk.size)}"
        out = await self._t.exec_stream(str(req.address), req.port, cmd, path, INSTALL_TIMEOUT)
        if "Success" not in out:
            m = _FAILURE_RE.search(out)
            if m and m.group(1) in _INCOMPATIBLE:
                raise _Stop("install", P + "install_incompatible")
            raise _Stop("install", P + "install_failed")
        await rep("install", "ok", None, version_code=req.apk.version_code)
        return req.apk.version_code

    async def _device_owner(self, req: ProvisionRequest, info, rep: Rep) -> bool:
        if req.device_owner == "skip":
            await rep("device_owner", "skipped", P + "device_owner_skipped")
            return False
        if info["ours"]:
            await rep("device_owner", "ok")
            return True
        await rep("device_owner", "running")
        if info["other"]:
            await rep("device_owner", "skipped", P + "owner_exists")
            return False
        if info["accounts"]:
            await rep("device_owner", "skipped", P + "accounts_present", accounts=info["accounts"])
            return False
        if int(info["users"]) > 1:
            await rep("device_owner", "skipped", P + "extra_users", users=info["users"])
            return False
        out = await self._sh(req, CMD_SET_OWNER)
        if "Success" in out:
            await rep("device_owner", "ok")  # ADB réseau reste allumé (décision assumée)
            return True
        if "already some accounts" in out:
            await rep("device_owner", "skipped", P + "accounts_present", accounts=-1)
        else:
            await rep("device_owner", "skipped", P + "dpm_refused")
        return False

    async def _soft_kiosk(self, req: ProvisionRequest, owner: bool, rep: Rep) -> bool:
        if owner or not req.soft_kiosk_fallback:
            await rep("soft_kiosk", "skipped")
            return False
        await rep("soft_kiosk", "running")
        home = await self._sh(req, CMD_HOME)
        overlay = await self._sh(req, CMD_OVERLAY)
        if _bad(home) or _bad(overlay):
            await rep("soft_kiosk", "failed", P + "soft_kiosk_failed")
            return False
        # Annonce : Accueil et Assistant restent utilisables sur la TV.
        await rep("soft_kiosk", "ok", P + "soft_kiosk_active")
        return True

    async def _configure(self, req: ProvisionRequest, rep: Rep) -> None:
        await rep("configure", "running")
        try:
            out = await self._sh(req, CMD_AWAKE)
        except AdbError:
            out = "Error"
        if _bad(out):
            await rep("configure", "failed", P + "configure_failed")  # non bloquant
        else:
            await rep("configure", "ok")

    async def _inject(self, req: ProvisionRequest, rep: Rep) -> None:
        await rep("inject", "running")
        cmd = (
            f"am start -n {PROVISION_ACTIVITY}"
            f" --es server_url {shlex.quote(str(req.server_url))}"
            f" --es pairing_ticket {shlex.quote(req.pairing_ticket.get_secret_value())}"
        )
        out = await self._sh(req, cmd)
        if _bad(out):
            raise _Stop("inject", P + "inject_failed")
        await rep("inject", "ok")

    async def _launch(self, req: ProvisionRequest, rep: Rep) -> None:
        await rep("launch", "running")
        if _bad(await self._sh(req, CMD_LAUNCH)):
            raise _Stop("launch", P + "launch_failed")
        await rep("launch", "ok")
