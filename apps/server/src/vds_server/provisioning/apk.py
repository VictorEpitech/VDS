"""Catalogue d'APK : `apk.json` généré par la CI ; seuls les fichiers listés sont servis."""

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vds_sdk.agent.provision import MAX_APK_SIZE, ApkRef

log = logging.getLogger("vds.apk")

PACKAGE: Literal["app.vds.player"] = "app.vds.player"
MANIFEST = "apk.json"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ApkEntry:
    version_code: int
    version_name: str
    sha256: str
    size: int
    path: Path

    def ref(self) -> ApkRef:
        return ApkRef(
            package=PACKAGE,
            version_code=self.version_code,
            version_name=self.version_name,
            sha256=self.sha256,
            size=self.size,
        )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _load_entry(root: Path, raw: object) -> ApkEntry | None:
    if not isinstance(raw, dict) or raw.get("package") != PACKAGE:
        return None
    code, name = raw.get("version_code"), raw.get("version_name")
    digest, size, file = raw.get("sha256"), raw.get("size"), raw.get("file")
    if not (isinstance(code, int) and not isinstance(code, bool) and code >= 1):
        return None
    if not (isinstance(name, str) and len(name) <= 32):
        return None
    if not (isinstance(digest, str) and _SHA.match(digest)):
        return None
    if not (isinstance(size, int) and 1 <= size <= MAX_APK_SIZE):
        return None
    if not (isinstance(file, str) and _FILE.match(file) and ".." not in file):
        return None
    path = (root / file).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != size:
        return None
    if _sha256(path) != digest:
        log.warning("apk sha256 mismatch version_code=%s", code)
        return None
    return ApkEntry(code, name, digest, size, path)


def load_entries(directory: Path) -> dict[int, ApkEntry]:
    root = directory.resolve()
    manifest = root / MANIFEST
    if not manifest.is_file():
        return {}
    try:
        items = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("apk.json unreadable")
        return {}
    out: dict[int, ApkEntry] = {}
    for raw in items if isinstance(items, list) else []:
        entry = _load_entry(root, raw)
        if entry is None:
            log.warning("apk entry rejected")
        elif entry.version_code not in out:
            out[entry.version_code] = entry
    return out


class ApkCatalog:
    """Chargé une fois au démarrage (sha256 recalculé) ; lecture seule ensuite."""

    def __init__(self, directory: Path | None) -> None:
        self._dir = directory
        self._entries: dict[int, ApkEntry] | None = None

    async def load(self) -> None:
        self._entries = (
            {} if self._dir is None else await asyncio.to_thread(load_entries, self._dir)
        )

    def entries(self) -> dict[int, ApkEntry]:
        if self._entries is None:  # accès avant le démarrage (tests, scripts) : chargement direct
            self._entries = {} if self._dir is None else load_entries(self._dir)
        return self._entries

    def latest(self) -> ApkEntry | None:
        entries = self.entries()
        return entries[max(entries)] if entries else None

    def get(self, version_code: int) -> ApkEntry | None:
        return self.entries().get(version_code)
