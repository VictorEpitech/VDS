"""Interface `ScreenProvisioner` (cible site-agent), contrat vds-core 1.3.0.

Le noyau ne connaît aucune plateforme : un module déclare la capacité `provision.<platform>`
et implémente `ScreenProvisioner`. Aucune URL d'APK ni secret en clair dans les journaux.
"""

import ipaddress
import re
from collections.abc import Awaitable, Callable
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, field_validator

from ..errors import ContractError

ProvisionStepId = Literal[
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
]
StepStatus = Literal["pending", "running", "waiting_user", "ok", "skipped", "failed"]

PROVISION_CAPABILITY_RE = re.compile(r"^provision\.[a-z][a-z0-9_-]*$")
TICKET_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")  # base64url uniquement
MAX_APK_SIZE = 200 * 1024 * 1024


def ensure_lan_address(ip: IPv4Address | IPv6Address) -> IPv4Address | IPv6Address:
    """Adresse d'un appareil du LAN : privée ou lien local ; ni boucle locale, ni publique."""
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    ok = (ip.is_private or ip.is_link_local) and not (
        ip.is_loopback or ip.is_unspecified or ip.is_multicast
    )
    if not ok:
        raise ValueError("provision.address_not_lan")
    return ip


def parse_lan_address(value: str) -> str:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("provision.address_invalid") from exc
    return str(ensure_lan_address(ip))


class ApkRef(BaseModel):
    """Référence d'APK fournie par le serveur : jamais une URL."""

    package: Literal["app.vds.player"]
    version_code: int = Field(ge=1)
    version_name: str = Field(max_length=32)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1, le=MAX_APK_SIZE)


class ProvisionRequest(BaseModel):
    provision_id: UUID
    screen_id: UUID
    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    address: IPv4Address | IPv6Address  # privée / lien local uniquement
    port: int = Field(default=5555, ge=1, le=65535)
    apk: ApkRef
    server_url: AnyHttpUrl
    pairing_ticket: SecretStr  # jamais journalisé
    device_owner: Literal["try", "skip"] = "try"
    soft_kiosk_fallback: bool = True
    from_step: ProvisionStepId | None = None

    @field_validator("address")
    @classmethod
    def _lan(cls, v: IPv4Address | IPv6Address) -> IPv4Address | IPv6Address:
        return ensure_lan_address(v)

    @field_validator("pairing_ticket")
    @classmethod
    def _ticket(cls, v: SecretStr) -> SecretStr:
        if not TICKET_RE.match(v.get_secret_value()):
            raise ValueError("provision.ticket_invalid")
        return v


class StepReport(BaseModel):
    step: ProvisionStepId
    status: StepStatus
    code: str | None = None  # "provision.<platform>.<raison>", clé i18n du module
    detail: dict[str, str | int | bool] = {}  # aucune donnée sensible


class ProvisionOutcome(BaseModel):
    ok: bool
    kiosk: Literal["device_owner", "soft", "none"]
    installed_version_code: int | None = None
    code: str | None = None


class ApkSource(Protocol):
    """Fourni par le runtime : télécharge, vérifie sha256/taille, met en cache par sha256."""

    async def fetch(self, apk: ApkRef) -> Path: ...


@runtime_checkable
class ScreenProvisioner(Protocol):
    platforms: ClassVar[frozenset[str]]  # ex. {"android"} ; capacité `provision.android`

    async def provision(
        self,
        req: ProvisionRequest,
        apk: ApkSource,
        report: Callable[[StepReport], Awaitable[None]],
    ) -> ProvisionOutcome: ...


def provision_platforms(capabilities: list[str]) -> frozenset[str]:
    return frozenset(
        c.removeprefix("provision.") for c in capabilities if c.startswith("provision.")
    )


def check_provisioner(instance: object, capabilities: list[str]) -> None:
    """Au chargement : `provision.<p>` déclaré => `ScreenProvisioner` couvrant <p>."""
    declared = provision_platforms(capabilities)
    if not declared:
        return
    if not isinstance(instance, ScreenProvisioner):
        raise ContractError("module.provisioner_missing")
    platforms = getattr(instance, "platforms", None)
    if not isinstance(platforms, frozenset) or not declared <= platforms:
        raise ContractError("module.provisioner_platform_mismatch")


__all__ = [
    "ApkRef",
    "ApkSource",
    "ProvisionOutcome",
    "ProvisionRequest",
    "ProvisionStepId",
    "ScreenProvisioner",
    "StepReport",
    "StepStatus",
    "check_provisioner",
    "ensure_lan_address",
    "parse_lan_address",
    "provision_platforms",
]
