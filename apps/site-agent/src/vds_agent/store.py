"""Stockage local SQLite : kv, outbox fiable, état des modules chiffré (Fernet)."""

import asyncio
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from vds_sdk.wire import MAX_UNACKED, Frame

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY, frame TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS module_state (
  module_id TEXT NOT NULL, key TEXT NOT NULL, value BLOB NOT NULL, PRIMARY KEY (module_id, key));
CREATE TABLE IF NOT EXISTS cmd_seen (cmd_id TEXT PRIMARY KEY, at REAL NOT NULL);
"""


def _load_or_create_key(data_dir: Path, configured: str | None) -> bytes:
    if configured:
        return configured.encode()
    path = data_dir / "state.key"
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


class Store:
    def __init__(self, data_dir: Path, state_key: str | None = None) -> None:
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._fernet = Fernet(_load_or_create_key(data_dir, state_key))
        self._db = sqlite3.connect(data_dir / "agent.db", check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = asyncio.Lock()
        self._out_lock = asyncio.Lock()
        (data_dir / "agent.db").chmod(0o600)

    async def _run(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        async with self._lock:
            return await asyncio.to_thread(self._exec, sql, args)

    def _exec(self, sql: str, args: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        cur = self._db.execute(sql, args)
        rows = cur.fetchall()
        self._db.commit()
        return rows

    def close(self) -> None:
        self._db.close()

    # kv (non secret)
    async def kv_get(self, key: str, default: Any = None) -> Any:
        rows = await self._run("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    async def kv_set(self, key: str, value: Any) -> None:
        await self._run("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value)))

    # outbox
    async def outbox_add(self, frame: Frame) -> Frame:
        """Attribue le prochain seq, persiste ; borne à MAX_UNACKED (purge des plus anciennes)."""
        async with self._out_lock:
            seq = int(await self.kv_get("out_seq", 0)) + 1
            stamped = frame.model_copy(update={"seq": seq})
            await self.kv_set("out_seq", seq)
            await self._run(
                "INSERT INTO outbox VALUES (?,?)", (seq, stamped.model_dump_json(exclude_none=True))
            )
            await self._run("DELETE FROM outbox WHERE seq <= ?", (seq - MAX_UNACKED,))
        return stamped

    async def outbox_ack(self, ack: int) -> None:
        await self._run("DELETE FROM outbox WHERE seq <= ?", (ack,))

    async def outbox_after(self, seq: int) -> list[Frame]:
        rows = await self._run("SELECT frame FROM outbox WHERE seq > ? ORDER BY seq", (seq,))
        return [Frame.model_validate_json(r[0]) for r in rows]

    # état des modules (chiffré)
    async def state_get(self, module_id: str, key: str, default: Any = None) -> Any:
        rows = await self._run(
            "SELECT value FROM module_state WHERE module_id=? AND key=?", (module_id, key)
        )
        if not rows:
            return default
        return json.loads(self._fernet.decrypt(rows[0][0]))

    async def state_set(self, module_id: str, key: str, value: Any) -> None:
        blob = self._fernet.encrypt(json.dumps(value).encode())
        await self._run(
            "INSERT OR REPLACE INTO module_state VALUES (?,?,?)", (module_id, key, blob)
        )

    async def state_delete(self, module_id: str, key: str | None = None) -> None:
        if key is None:
            await self._run("DELETE FROM module_state WHERE module_id=?", (module_id,))
        else:
            await self._run(
                "DELETE FROM module_state WHERE module_id=? AND key=?", (module_id, key)
            )

    async def wipe_secrets(self) -> None:
        """Révocation (4410) : efface états de modules, outbox, kv."""
        for table in ("module_state", "outbox", "kv", "cmd_seen"):
            await self._run(f"DELETE FROM {table}")  # noqa: S608 - noms constants

    # idempotence des commandes
    async def cmd_seen(self, cmd_id: str) -> bool:
        return bool(await self._run("SELECT 1 FROM cmd_seen WHERE cmd_id=?", (cmd_id,)))

    async def cmd_mark(self, cmd_id: str, now: float) -> None:
        await self._run("INSERT OR REPLACE INTO cmd_seen VALUES (?,?)", (cmd_id, now))
        await self._run("DELETE FROM cmd_seen WHERE at < ?", (now - 7 * 86400,))


class ModuleState:
    """`StateStore` d'un module (contrat vds_sdk.agent.StateStore)."""

    def __init__(self, store: Store, module_id: str) -> None:
        self._s = store
        self._m = module_id

    async def get(self, key: str, default: Any = None) -> Any:
        return await self._s.state_get(self._m, key, default)

    async def set(self, key: str, value: Any) -> None:
        await self._s.state_set(self._m, key, value)

    async def delete(self, key: str) -> None:
        await self._s.state_delete(self._m, key)
