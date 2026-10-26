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
    """mode: open (sans AUTH) | auth | prompt (invite en attente) | deny | hang | stls."""

    def __init__(self, mode: str = "auth", wakefulness: str = "Awake") -> None:
        self.mode = mode
        self.wakefulness = wakefulness
        self.accepted: list[RSAPublicNumbers] = []
        self.commands: list[str] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self.on_command: Callable[[str], None] | None = None
        # provisioning
        self.sdk = "30"
        self.model = "Fake TV"
        self.accounts = 0
        self.users = 1
        self.version_code = 0  # 0 = app absente
        self.install_as = 7
        self.owner = ""  # sortie de `dpm list-owners`
        self.install_output = "Success\n"
        self.set_owner_output = "Success: Device owner set\n"
        self.received_apk = bytearray()
        self.adb_cut = False  # Device Owner : adbd coupé après le lancement de MainActivity
        self.accept_event = asyncio.Event()  # mode 'prompt' : l'utilisateur accepte l'invite

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
            if self.adb_cut:
                return
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
            if line.startswith("exec:cmd package install"):
                size = int(line.rsplit(" ", 1)[1])
                w.write(pack(A_OKAY, 77, a0))
                await w.drain()
                while len(self.received_apk) < size:
                    c, _x, _y, chunk = await self._read(r)
                    if c == A_WRTE:
                        self.received_apk += chunk
                        w.write(pack(A_OKAY, 77, a0))
                        await w.drain()
                out = self.install_output.encode()
                if out.startswith(b"Success"):
                    self.version_code = self.install_as
                w.write(pack(A_WRTE, 77, a0, out))
                await w.drain()
                await self._read(r)
                w.write(pack(A_CLSE, 77, a0))
                await w.drain()
                return
            scripted = self._scripted(line)
            if scripted is not None:
                out = scripted.encode()
            elif line.startswith("shell:dumpsys power"):
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

    def _scripted(self, line: str) -> str | None:
        cmd = line.removeprefix("shell:")
        if cmd == "getprop ro.build.version.sdk":
            return self.sdk + "\n"
        if cmd == "getprop ro.product.model":
            return self.model + "\n"
        if cmd == "dumpsys account":
            return f"User UserInfo{{0:Owner:13}}:\n  Accounts: {self.accounts}\n"
        if cmd == "pm list users":
            return "Users:\n" + "".join(
                f"\tUserInfo{{{i}:U{i}:13}} running\n" for i in range(self.users)
            )
        if cmd.startswith("pm list packages"):
            if self.version_code:
                return f"package:app.vds.player versionCode:{self.version_code}\n"
            return ""
        if cmd == "dpm list-owners":
            return self.owner
        if cmd.startswith("dpm set-device-owner"):
            if self.set_owner_output.startswith("Success"):
                self.owner = (
                    "User 0: admin=ComponentInfo{app.vds.player/app.vds.player.AdminReceiver}\n"
                )
            return self.set_owner_output
        if cmd == "settings put global adb_enabled 0":
            self.adb_cut = True
            return ""
        if (
            cmd.startswith("am start")
            and "ProvisionActivity" in cmd
            and "AdminReceiver" in self.owner
        ):
            self.adb_cut = True  # MainActivity applique DISALLOW_DEBUGGING_FEATURES
        if cmd.startswith(("cmd package set-home-activity", "appops set", "settings put")):
            return "Success\n" if "home" in cmd else ""
        if cmd.startswith("am start"):
            return "Starting: Intent { cmp=app.vds.player/... }\n"
        return None

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
        if self.mode == "prompt":
            await self.accept_event.wait()
        self.pending_key = parse_android_pubkey(data)
        self.accepted.append(self.pending_key)
        return True
