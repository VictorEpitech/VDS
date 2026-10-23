"""Découverte des modules intégrés (`<modules_dir>/*/module.json`), sans effet de bord."""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from vds_sdk import CORE_API_VERSION, ContractError, ModuleManifest, validate_config_schema

log = logging.getLogger("vds.registry")


@dataclass(frozen=True)
class Discovered:
    path: Path
    manifest: ModuleManifest
    config_schema: dict[str, Any] | None
    checksum: str
    error: str | None = None  # code stable : le module est alors `incompatible`


def discover(modules_dir: Path, core_version: str = CORE_API_VERSION) -> list[Discovered]:
    found: list[Discovered] = []
    if not modules_dir.is_dir():
        return found
    for manifest_path in sorted(modules_dir.glob("*/module.json")):
        try:
            found.append(_load(manifest_path, core_version))
        except (ValidationError, ValueError, OSError):
            # Manifeste illisible : pas d'identité fiable, on ne peut pas l'enregistrer.
            log.error("module.manifest_invalid", extra={"path": str(manifest_path)})
    return found


def _load(path: Path, core_version: str) -> Discovered:
    raw_text = path.read_text(encoding="utf-8")
    manifest = ModuleManifest.model_validate(json.loads(raw_text))
    digest = hashlib.sha256(raw_text.encode())
    schema: dict[str, Any] | None = None
    error: str | None = None
    try:
        manifest.check_core_compat(core_version)
        if manifest.config is not None:
            ref = (path.parent / manifest.config.ref).resolve()
            if not ref.is_relative_to(path.parent.resolve()):
                raise ContractError("module.config_ref_outside")
            schema_text = ref.read_text(encoding="utf-8")
            digest.update(schema_text.encode())
            schema = json.loads(schema_text)
            validate_config_schema(schema)
    except ContractError as e:
        error = e.code
    return Discovered(path.parent, manifest, schema, digest.hexdigest(), error)
