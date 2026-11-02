"""Catalogue d'APK : `apk.json` généré par la CI ; seuls les fichiers listés sont servis."""

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vds_sdk.agent.provision import MAX_APK_SIZE, ApkRef

log = logging.getLogger("vds.apk")

PACKAGE: Literal["app.vds.player"] = "app.vds.player"
MANIFEST = "apk.json"
RECHECK_S = 60.0  # relecture de apk.json : au plus un contrôle de mtime par minute
DEFAULT_DIR = Path("/opt/vds/apk")  # dossier de l'image ; tout autre dossier = VDS_APK_DIR
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


def _load_entry(root: Path, raw: object, signer: str | None = None) -> ApkEntry | None:
    if not isinstance(raw, dict) or raw.get("package") != PACKAGE:
        return None
    if signer is not None and str(raw.get("signer_sha256", "")).lower() != signer:
        log.warning("apk signer mismatch")
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


def load_entries(directory: Path, signer: str | None = None) -> dict[int, ApkEntry]:
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
        entry = _load_entry(root, raw, signer)
        if entry is None:
            log.warning("apk entry rejected")
        elif entry.version_code not in out:
            out[entry.version_code] = entry
    return out


def _mtime(directory: Path) -> int | None:
    try:
        return (directory / MANIFEST).stat().st_mtime_ns
    except OSError:
        return None


class ApkCatalog:
    """Chargé au démarrage (sha256 recalculé), relu quand `apk.json` change (contrôle ≤ 1/min)."""

    def __init__(
        self,
        directory: Path | None,
        signer: str | None = None,
        *,
        recheck_s: float = RECHECK_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._dir = directory
        self._signer = signer.lower() if signer else None
        self._recheck_s, self._clock = recheck_s, clock
        self._entries: dict[int, ApkEntry] | None = None
        self._mtime: int | None = None
        self._checked: float | None = None

    @property
    def source(self) -> Literal["image", "dir"]:
        return "image" if self._dir is None or self._dir == DEFAULT_DIR else "dir"

    async def load(self) -> None:
        if self._dir is None:
            self._entries = {}
            return
        self._mtime = _mtime(self._dir)
        self._checked = self._clock()
        self._entries = await asyncio.to_thread(load_entries, self._dir, self._signer)
        if self._entries and self._signer is None:
            log.warning("apk catalog present but VDS_APK_SIGNER_SHA256 is not set: no signer pin")

    async def refresh(self) -> None:
        """Relit `apk.json` si son mtime a changé (mêmes vérifications qu'au démarrage)."""
        if self._dir is None or self._entries is None:
            return
        now = self._clock()
        if self._checked is not None and now - self._checked < self._recheck_s:
            return
        self._checked = now
        mtime = _mtime(self._dir)
        if mtime != self._mtime:
            self._mtime = mtime
            self._entries = await asyncio.to_thread(load_entries, self._dir, self._signer)

    def entries(self) -> dict[int, ApkEntry]:
        if self._entries is None:  # accès avant le démarrage (tests, scripts) : chargement direct
            self._entries = {} if self._dir is None else load_entries(self._dir, self._signer)
            self._mtime = None if self._dir is None else _mtime(self._dir)
        return self._entries

    def latest(self) -> ApkEntry | None:
        entries = self.entries()
        return entries[max(entries)] if entries else None

    def get(self, version_code: int) -> ApkEntry | None:
        return self.entries().get(version_code)
