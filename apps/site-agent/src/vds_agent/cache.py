"""Cache média de site : fichiers adressés par sha256, téléchargement vérifié, éviction LRU."""

import contextlib
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("vds.agent.cache")

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PATH_RE = re.compile(r"^media/[0-9a-fA-F-]{32,36}/[a-z0-9-]{1,40}$")
CHUNK = 1 << 16


@dataclass(frozen=True)
class SyncResult:
    items: int
    bytes: int
    missing: int


class MediaCache:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max_bytes
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, sha256: str) -> Path | None:
        """Chemin d'un contenu ; None si l'empreinte n'est pas un sha256 hexadécimal minuscule."""
        return self.root / sha256 if SHA_RE.match(sha256) else None

    def has(self, sha256: str) -> bool:
        p = self.path(sha256)
        return p is not None and p.is_file()

    def touch(self, sha256: str) -> None:
        p = self.path(sha256)
        if p is not None:
            with contextlib.suppress(OSError):
                os.utime(p)

    def total_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.root.iterdir() if SHA_RE.match(f.name))

    async def fetch(
        self, client: httpx.AsyncClient, base: str, token: str, entry: dict[str, Any]
    ) -> bool:
        """Télécharge une entrée du manifeste ; vrai si le fichier est présent et intègre."""
        sha, size, rel = str(entry["sha256"]), int(entry["bytes"]), str(entry["path"])
        dest = self.path(sha)
        if dest is None or not PATH_RE.match(rel):
            return False
        if dest.is_file():
            self.touch(sha)
            return True
        tmp = self.root / f".{sha}.part"
        h = hashlib.sha256()
        n = 0
        try:
            async with client.stream(
                "GET", f"{base}/{rel}", headers={"Authorization": f"Bearer {token}"}
            ) as r:
                r.raise_for_status()
                with tmp.open("wb") as fh:
                    async for chunk in r.aiter_bytes(CHUNK):
                        n += len(chunk)
                        if n > size:  # plus long que déclaré : on coupe sans tout lire
                            break
                        h.update(chunk)
                        fh.write(chunk)
            if n != size or h.hexdigest() != sha:
                log.warning("cache.integrity_failed", extra={"sha256": sha})
                return False
            tmp.replace(dest)
            return True
        except (httpx.HTTPError, OSError):
            log.warning("cache.download_failed", extra={"sha256": sha})
            return False
        finally:
            tmp.unlink(missing_ok=True)

    def evict(self, keep: set[str]) -> int:
        """Au-delà du quota, supprime d'abord les fichiers hors manifeste, les moins récents."""
        files = [f for f in self.root.iterdir() if SHA_RE.match(f.name)]
        total = sum(f.stat().st_size for f in files)
        removed = 0
        for f in sorted((f for f in files if f.name not in keep), key=lambda f: f.stat().st_mtime):
            if total <= self.max_bytes:
                break
            total -= f.stat().st_size
            f.unlink(missing_ok=True)
            removed += 1
        for part in self.root.glob(".*.part"):  # restes d'un arrêt brutal
            if time.time() - part.stat().st_mtime > 3600:
                part.unlink(missing_ok=True)
        return removed

    async def sync(
        self, client: httpx.AsyncClient, base: str, token: str, manifest: dict[str, Any]
    ) -> SyncResult:
        """Un seul téléchargement par contenu, quel que soit le nombre d'écrans qui l'utilisent."""
        wanted: dict[str, dict[str, Any]] = {}
        for e in manifest.get("items", []):
            wanted.setdefault(str(e["sha256"]), e)
        missing = 0
        for e in wanted.values():
            if not await self.fetch(client, base, token, e):
                missing += 1
        self.evict(set(wanted))
        present = [p for s in wanted if (p := self.path(s)) is not None and p.is_file()]
        size = sum(p.stat().st_size for p in present)
        return SyncResult(items=len(present), bytes=size, missing=missing)


async def fetch_manifest(client: httpx.AsyncClient, base: str, token: str) -> dict[str, Any]:
    r = await client.get(
        f"{base}/api/v1/agent/cache/manifest", headers={"Authorization": f"Bearer {token}"}
    )
    r.raise_for_status()
    body = r.json()
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ValueError("cache.manifest_invalid")
    return body
