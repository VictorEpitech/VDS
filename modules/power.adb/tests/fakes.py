"""Faux appareil ADB : serveur TCP minimal CNXN/AUTH/OPEN shell:."""

import asyncio
import struct
from collections.abc import Callable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, utils
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

A_CNXN, A_AUTH, A_OPEN = 0x4E584E43, 0x48545541, 0x4E45504F
A_OKAY, A_CLSE, A_WRTE = 0x59414B4F, 0x45534C43, 0x45545257
HEADER = struct.Struct("<6I")


def pack(cmd: int, a0: int, a1: int, data: bytes = b"") -> bytes:
    return HEADER.pack(cmd, a0, a1, len(data), sum(data), cmd ^ 0xFFFFFFFF) + data


def parse_android_pubkey(b64line: bytes) -> RSAPublicNumbers:
    import base64

    blob = base64.b64decode(b64line.split(b" ")[0])
    n = int.from_bytes(blob[8:264], "little")
    e = struct.unpack("<I", blob[520:524])[0]
    return RSAPublicNumbers(e, n)


class FakeAdbTv:
    """mode: 'open' (sans AUTH) | 'auth' (clé à accepter) | 'deny' | 'hang' | 'stls'."""

    def __init__(self, mode: str = "auth", wakefulness: str = "Awake") -> None:
        self.mode = mode
        self.wakefulness = wakefulness
        self.accepted: list[RSAPublicNumbers] = []
        self.commands: list[str] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self.on_command: Callable[[str], None] | None = None

    async def __aenter__(self) -> "FakeAdbTv":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *a: object) -> None:
        assert self._server
        self._server.close()
        await self._server.wait_closed()

    async def _read(self, r: asyncio.StreamReader) -> tuple[int, int, int, bytes]:
        cmd, a0, a1, ln, _c, _m = HEADER.unpack(await r.readexactly(24))
        return cmd, a0, a1, (await r.readexactly(ln) if ln else b"")

    async def _handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        try:
            if self.mode == "hang":
                await asyncio.sleep(1.5)
                return
            await self._read(r)  # CNXN client
            if self.mode == "stls":
                w.write(pack(0x534C5453, 0x01000000, 0))
                await w.drain()
                return
            if self.mode != "open" and not await self._auth(r, w):
                return
            w.write(pack(A_CNXN, 0x01000001, 4096, b"device::ro.product.name=fake;\x00"))
            await w.drain()
            cmd, a0, _a1, data = await self._read(r)
            if cmd != A_OPEN:
                return
            line = data.rstrip(b"\x00").decode()
            self.commands.append(line)
            out = b""
            if line.startswith("shell:dumpsys power"):
                out = (
                    b"Power Manager State:\n  mWakefulness="
                    + self.wakefulness.encode()
                    + b"\n"
                    + b"x" * 5000
                    + b"\n"
                )
            elif line.startswith("shell:getprop"):
                out = b"TCL\n"
            elif "KEYCODE_SLEEP" in line:
                self.wakefulness = "Asleep"
            elif "KEYCODE_WAKEUP" in line:
                self.wakefulness = "Awake"
            w.write(pack(A_OKAY, 77, a0))
            for i in range(0, len(out), 2048):
                w.write(pack(A_WRTE, 77, a0, out[i : i + 2048]))
                await w.drain()
                await self._read(r)  # OKAY
            w.write(pack(A_CLSE, 77, a0))
            await w.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            w.close()

    async def _auth(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> bool:
        token = b"0123456789abcdef0123"
        w.write(pack(A_AUTH, 1, 0, token))
        await w.drain()
        cmd, a0, _a1, data = await self._read(r)
        for key in self.accepted:
            try:
                key.public_key().verify(
                    data, token, padding.PKCS1v15(), utils.Prehashed(hashes.SHA1())
                )
                return True
            except Exception:
                continue
        w.write(pack(A_AUTH, 1, 0, token))
        await w.drain()
        cmd, a0, _a1, data = await self._read(r)
        if a0 != 3 or self.mode == "deny":
            w.write(pack(A_AUTH, 1, 0, token))
            await w.drain()
            return False
        self.pending_key = parse_android_pubkey(data)
        self.accepted.append(self.pending_key)
        return True
