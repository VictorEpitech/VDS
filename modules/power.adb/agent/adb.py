"""Client ADB minimal (TCP, protocole CNXN/AUTH/OPEN) : exécute une commande `shell:`.

Limite assumée : pas de TLS (STLS, débogage sans fil Android 11+), pas de shell_v2.
"""

import asyncio
import base64
import contextlib
import struct
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

A_CNXN, A_AUTH, A_OPEN = 0x4E584E43, 0x48545541, 0x4E45504F
A_OKAY, A_CLSE, A_WRTE, A_STLS = 0x59414B4F, 0x45534C43, 0x45545257, 0x534C5453
AUTH_TOKEN, AUTH_SIGNATURE, AUTH_RSAPUBLICKEY = 1, 2, 3
VERSION = 0x01000001
MAX_DATA = 256 * 1024
HEADER = struct.Struct("<6I")


class AdbError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AdbTransport(Protocol):
    async def shell(self, host: str, port: int, command: str, timeout: float) -> str: ...


def generate_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def android_pubkey(key: rsa.RSAPrivateKey, name: str = "vds-agent@vds") -> bytes:
    """Clé publique au format ADB (struct RSAPublicKey 2048 bits) en base64 + nom."""
    nums = key.public_key().public_numbers()
    n, e = nums.n, nums.e
    r32 = 1 << 32
    n0inv = (-pow(n, -1, r32)) % r32
    rr = pow(1 << 2048, 2, n)
    blob = struct.pack("<II", 64, n0inv)
    blob += n.to_bytes(256, "little") + rr.to_bytes(256, "little") + struct.pack("<I", e)
    return base64.b64encode(blob) + b" " + name.encode() + b"\x00"


def sign_token(key: rsa.RSAPrivateKey, token: bytes) -> bytes:
    return key.sign(token, padding.PKCS1v15(), utils.Prehashed(hashes.SHA1()))  # noqa: S303


def pack(cmd: int, arg0: int, arg1: int, data: bytes = b"") -> bytes:
    return HEADER.pack(cmd, arg0, arg1, len(data), sum(data) & 0xFFFFFFFF, cmd ^ 0xFFFFFFFF) + data


async def read_msg(r: asyncio.StreamReader) -> tuple[int, int, int, bytes]:
    cmd, a0, a1, ln, _crc, magic = HEADER.unpack(await r.readexactly(HEADER.size))
    if magic != cmd ^ 0xFFFFFFFF or ln > MAX_DATA:
        raise AdbError("power.adb.protocol")
    return cmd, a0, a1, await r.readexactly(ln) if ln else b""


class TcpAdbTransport:
    def __init__(self, private_key_pem: str) -> None:
        loaded = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        assert isinstance(loaded, rsa.RSAPrivateKey)  # noqa: S101
        self._key = loaded
        self._auth_pending = False

    async def shell(self, host: str, port: int, command: str, timeout: float) -> str:
        self._auth_pending = False
        try:
            return await asyncio.wait_for(self._shell(host, port, command, timeout), timeout)
        except TimeoutError as e:
            code = "power.adb.unauthorized" if self._auth_pending else "power.adb.timeout"
            raise AdbError(code) from e
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
            raise AdbError("power.adb.unreachable") from e

    async def _connect(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.write(pack(A_CNXN, VERSION, MAX_DATA, b"host::\x00"))
        await w.drain()
        sent_sig = sent_pub = False
        while True:
            cmd, a0, _a1, data = await read_msg(r)
            if cmd == A_CNXN:
                return
            if cmd == A_STLS:
                raise AdbError("power.adb.tls_required")
            if cmd != A_AUTH or a0 != AUTH_TOKEN:
                raise AdbError("power.adb.protocol")
            if not sent_sig:
                sent_sig = True
                w.write(pack(A_AUTH, AUTH_SIGNATURE, 0, sign_token(self._key, data)))
            elif not sent_pub:
                sent_pub = self._auth_pending = True
                w.write(pack(A_AUTH, AUTH_RSAPUBLICKEY, 0, android_pubkey(self._key)))
            else:
                raise AdbError("power.adb.unauthorized")
            await w.drain()  # puis attente (bornée par `timeout`) de l'acceptation sur la TV

    async def _shell(self, host: str, port: int, command: str, timeout: float) -> str:
        r, w = await asyncio.open_connection(host, port)
        try:
            await self._connect(r, w)
            w.write(pack(A_OPEN, 1, 0, f"shell:{command}".encode() + b"\x00"))
            await w.drain()
            out = bytearray()
            remote = 0
            while True:
                cmd, a0, a1, data = await read_msg(r)
                if cmd == A_OKAY:
                    remote = a0
                elif cmd == A_WRTE:
                    remote = a0
                    out += data
                    w.write(pack(A_OKAY, 1, remote))
                    await w.drain()
                elif cmd == A_CLSE:
                    return out.decode("utf-8", "replace")
        finally:
            w.close()
            with contextlib.suppress(OSError):
                await w.wait_closed()
