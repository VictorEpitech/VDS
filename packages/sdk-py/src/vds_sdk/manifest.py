"""Modèles Pydantic du manifeste `module.json` (contrat vds-core 1.3.0)."""

import json
import re
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import CORE_API_VERSION
from .errors import ContractError, IncompatibleCore
from .events import EVENT_NAME_RE
from .permissions import Permission
from .versions import is_semver, parse_range, satisfies

Kind = Literal[
    "player-adapter",
    "power-driver",
    "content-type",
    "trigger",
    "notifier",
    "auth-provider",
    "theme",
    "storage",
]
Target = Literal["server", "site-agent", "admin", "player"]
PROVISION_CAPABILITY_RE = re.compile(r"^provision\.[a-z][a-z0-9_-]*$")
POWER_CAPABILITIES = frozenset({"power.on", "power.off", "power.state", "power.input"})
PYTHON_TARGETS = frozenset({"server", "site-agent"})

MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9]*\.[a-z0-9][a-z0-9-]*$")
ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
CONTENT_PLAYER_ELEMENT_RE = re.compile(r"^vds-content-[a-z0-9]+(-[a-z0-9]+)*$")
CONTENT_MEDIA_ACCEPTS_RE = re.compile(r"^[a-z]+/[a-z0-9.+*-]+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalizedText(_Strict):
    fr: str = Field(min_length=1)
    en: str = Field(min_length=1)


class Events(_Strict):
    listens: list[str] = []
    emits: list[str] = []

    @field_validator("listens", "emits")
    @classmethod
    def _names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not EVENT_NAME_RE.match(name):
                raise ValueError("module.event_name_invalid")
        return v


class ConfigRef(_Strict):
    ref: str = Field(alias="$ref", min_length=1)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ThemeLogo(_Strict):
    light: str
    dark: str


class ThemeSpec(_Strict):
    css: str
    fonts: list[str] = []
    logo: ThemeLogo
    icons: Literal["material-symbols", "lucide"]
    color_schemes: list[Literal["light", "dark"]] = Field(min_length=1)


class ContentPlayer(_Strict):
    """Spécification du lecteur pour un type de contenu."""

    element: str

    @field_validator("element")
    @classmethod
    def _element(cls, v: str) -> str:
        if not CONTENT_PLAYER_ELEMENT_RE.match(v):
            raise ValueError("module.content_player_element_invalid")
        return v

    script: str

    @field_validator("script")
    @classmethod
    def _script(cls, v: str) -> str:
        if v.startswith("/") or ".." in v:
            raise ValueError("module.content_player_script_invalid")
        return v


class ContentMedia(_Strict):
    """Spécification des formats supportés par un type de contenu."""

    accepts: list[str]

    @field_validator("accepts")
    @classmethod
    def _accepts(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("module.content_media_accepts_empty")
        for accept in v:
            if not CONTENT_MEDIA_ACCEPTS_RE.match(accept):
                raise ValueError("module.content_media_accept_invalid")
        return v


class ContentSpec(_Strict):
    """Spécification du type de contenu d'un module."""

    item_schema: str

    @field_validator("item_schema")
    @classmethod
    def _item_schema(cls, v: str) -> str:
        if v.startswith("/") or ".." in v:
            raise ValueError("module.content_item_schema_invalid")
        if not v.endswith(".json"):
            raise ValueError("module.content_item_schema_not_json")
        return v

    player: ContentPlayer
    media: ContentMedia | None = None
    requires_network: bool = False


class ModuleManifest(_Strict):
    id: str
    version: str
    core: str
    kind: Kind
    name: LocalizedText
    description: LocalizedText
    targets: list[Target] = Field(min_length=1)
    entrypoints: dict[Target, str] = {}
    requires: dict[str, str] = {}
    capabilities: list[str] = []
    permissions: list[Permission] = []
    events: Events = Events()
    config: ConfigRef | None = None
    assets: str | None = None
    i18n: list[str] = ["fr", "en"]
    author: str
    signature: str | None = None
    theme: ThemeSpec | None = None
    content: ContentSpec | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not MODULE_ID_RE.match(v):
            raise ValueError("module.id_invalid")
        return v

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if not is_semver(v):
            raise ValueError("module.version_invalid")
        return v

    @field_validator("core")
    @classmethod
    def _core(cls, v: str) -> str:
        try:
            parse_range(v)
        except ValueError as e:
            raise ValueError("module.core_range_invalid") from e
        return v

    @field_validator("requires")
    @classmethod
    def _requires(cls, v: dict[str, str]) -> dict[str, str]:
        for mid, spec in v.items():
            if not MODULE_ID_RE.match(mid):
                raise ValueError("module.id_invalid")
            try:
                parse_range(spec)
            except ValueError as e:
                raise ValueError("module.requires_range_invalid") from e
        return v

    @model_validator(mode="after")
    def _coherence(self) -> Self:
        if len(set(self.permissions)) != len(self.permissions):
            raise ValueError("module.permission_duplicate")
        if "users.provision" in self.permissions and self.kind != "auth-provider":
            raise ValueError("module.permission_kind_forbidden")
        for target, ep in self.entrypoints.items():
            if target not in PYTHON_TARGETS:
                raise ValueError("module.entrypoint_target_invalid")
            if target not in self.targets:
                raise ValueError("module.entrypoint_target_undeclared")
            if not ENTRYPOINT_RE.match(ep):
                raise ValueError("module.entrypoint_invalid")
        if self.kind == "theme":
            if self.theme is None:
                raise ValueError("module.theme_missing")
            if self.entrypoints:
                raise ValueError("module.theme_has_entrypoint")
        else:
            if self.theme is not None:
                raise ValueError("module.theme_unexpected")
            for t in self.targets:
                if t in PYTHON_TARGETS and t not in self.entrypoints:
                    raise ValueError("module.entrypoint_missing")
        if self.kind in ("power-driver", "notifier"):
            if not (satisfies("1.2.0", self.core) or satisfies("1.3.0", self.core)) or satisfies(
                "1.1.0", self.core
            ):
                raise ValueError("contract.kind_requires_1_2")
        if self.kind == "power-driver":
            if not set(self.targets) <= {"site-agent", "player"}:
                raise ValueError("module.power_driver_target_invalid")
            provision = [c for c in self.capabilities if c.startswith("provision.")]
            if not set(self.capabilities) - set(provision) <= POWER_CAPABILITIES:
                raise ValueError("module.power_driver_capability_invalid")
            if provision:
                if not all(PROVISION_CAPABILITY_RE.match(c) for c in provision):
                    raise ValueError("module.provision_capability_invalid")
                if not satisfies("1.3.0", self.core) or satisfies("1.2.0", self.core):
                    raise ValueError("contract.provision_requires_1_3")
                if "screens.provision" not in self.permissions:
                    raise ValueError("module.provision_permission_missing")
                if "site-agent" not in self.targets:
                    raise ValueError("module.provision_target_invalid")
        if self.kind == "notifier" and self.targets != ["server"]:
            raise ValueError("module.notifier_target_invalid")
        if self.kind == "content-type":
            if self.content is None:
                raise ValueError("module.content_type_missing_content")
            if not satisfies("1.1.0", self.core):
                raise ValueError("module.content_type_core_incompatible")
            if satisfies("1.0.0", self.core):
                raise ValueError("module.content_type_core_incompatible")
        else:
            if self.content is not None:
                raise ValueError("module.content_type_unexpected")
        return self

    def check_core_compat(self, core_version: str = CORE_API_VERSION) -> None:
        """Lève IncompatibleCore si le noyau n'est pas dans l'intervalle `core`."""
        if not satisfies(core_version, self.core):
            raise IncompatibleCore(detail=f"{self.id} requires {self.core}, core {core_version}")


def load_manifest(path: Path, *, core_version: str = CORE_API_VERSION) -> ModuleManifest:
    """Lit un module.json, valide le modèle, la compatibilité noyau et le schéma de config."""
    from .config_schema import validate_config_schema

    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    manifest = ModuleManifest.model_validate(raw)
    manifest.check_core_compat(core_version)
    if manifest.config is not None:
        ref = (path.parent / manifest.config.ref).resolve()
        if not ref.is_relative_to(path.parent.resolve()):
            raise ContractError("module.config_ref_outside")
        validate_config_schema(json.loads(ref.read_text(encoding="utf-8")))
    if manifest.content is not None:
        item_schema_path = path.parent / manifest.content.item_schema
        if not item_schema_path.exists():
            raise ContractError("module.content_item_schema_missing")
        validate_config_schema(json.loads(item_schema_path.read_text(encoding="utf-8")))
        player_script_path = path.parent / manifest.content.player.script
        if not player_script_path.exists():
            raise ContractError("module.content_player_script_missing")
    return manifest


__all__ = ["ModuleManifest", "load_manifest"]
