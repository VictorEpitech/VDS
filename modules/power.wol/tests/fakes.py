"""Socket UDP de capture du paquet magique."""

import asyncio


class UdpCapture(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.packets: list[bytes] = []
        self.port = 0
        self._transport: asyncio.DatagramTransport | None = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.packets.append(data)

    async def __aenter__(self) -> "UdpCapture":
        loop = asyncio.get_running_loop()
        tr, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=("127.0.0.1", 0))
        self._transport = tr  # type: ignore[assignment]
        self.port = tr.get_extra_info("sockname")[1]
        return self

    async def __aexit__(self, *a: object) -> None:
        assert self._transport
        self._transport.close()
