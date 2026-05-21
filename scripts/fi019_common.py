from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Optional

from bleak import BleakClient, BleakScanner

from instax_lab.protocol import MINI_EVO_ADDR, NOTIFY_UUID, WRITE_UUID, make_packet


@dataclass
class SupportInfo03:
    raw: bytes
    transfer_count: Optional[int]
    print_count: Optional[int]


class LinkClient:
    def __init__(self, address: str = MINI_EVO_ADDR, verbose: bool = True):
        self.address = address
        self.verbose = verbose
        self._client: Optional[BleakClient] = None
        self._rxq: asyncio.Queue[tuple[int, int, bytes]] = asyncio.Queue()
        self._buf = bytearray()

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        self._buf.extend(data)
        while len(self._buf) >= 4:
            if self._buf[0] != 0x61 or self._buf[1] != 0x42:
                self._buf.clear()
                return
            total = struct.unpack_from(">H", self._buf, 2)[0]
            if len(self._buf) < total:
                return
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            op1, op2 = frame[4], frame[5]
            payload = frame[6:total - 1] if total > 7 else b""
            self._rxq.put_nowait((op1, op2, payload))

    async def connect(self) -> None:
        dev = await BleakScanner.find_device_by_filter(
            lambda d, _a: d.address.upper() == self.address.upper(),
            timeout=20,
        )
        if not dev:
            raise RuntimeError(f"Device not found: {self.address}")

        last_err: Exception | None = None
        for attempt in range(1, 6):
            try:
                self._client = BleakClient(self.address, timeout=20)
                await self._client.connect()
                self.log(f"connected {dev.address} mtu={self._client.mtu_size}")

                # WinRT can report connected but still reject notify until
                # GATT services are resolved. Bleak API differs by version.
                get_services = getattr(self._client, "get_services", None)
                if callable(get_services):
                    await get_services()
                await asyncio.sleep(1.0)

                # Retry notify briefly in case the stack is still settling.
                for n_try in range(1, 4):
                    try:
                        await self._client.start_notify(NOTIFY_UUID, self._on_notify)
                        self.log("notify subscribed")
                        return
                    except Exception as e:
                        last_err = e
                        if n_try == 3:
                            raise
                        self.log(f"start_notify retry {n_try}/3: {e}")
                        await asyncio.sleep(1.0)
            except Exception as e:
                last_err = e
                self.log(f"connect attempt {attempt}/5 failed: {e}")
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                await asyncio.sleep(2.0)

        raise RuntimeError(f"Unable to establish stable notify session: {last_err}")

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._buf.clear()

    async def flush(self) -> None:
        while True:
            try:
                self._rxq.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def write(self, op1: int, op2: int, payload: bytes = b"") -> None:
        if not self._client:
            raise RuntimeError("not connected")
        pkt = make_packet(op1, op2, payload)
        for off in range(0, len(pkt), 182):
            await self._client.write_gatt_char(
                WRITE_UUID,
                bytearray(pkt[off:off + 182]),
                response=False,
            )

    async def write_raw(self, payload: bytes) -> None:
        if not self._client:
            raise RuntimeError("not connected")
        # Write the raw payload directly (no Link protocol framing)
        for off in range(0, len(payload), 182):
            await self._client.write_gatt_char(
                WRITE_UUID,
                bytearray(payload[off:off + 182]),
                response=False,
            )

    async def recv(self, timeout: float = 5.0) -> tuple[int, int, bytes]:
        return await asyncio.wait_for(self._rxq.get(), timeout=timeout)

    async def exchange(
        self,
        op1: int,
        op2: int,
        payload: bytes = b"",
        timeout: float = 5.0,
    ) -> tuple[int, int, bytes]:
        await self.write(op1, op2, payload)
        return await self.recv(timeout=timeout)

    async def hello(self) -> None:
        await self.exchange(0x00, 0x00, timeout=3.0)

    async def read_device_info(self, info_type: int) -> str:
        _, _, p = await self.exchange(0x00, 0x01, bytes([info_type]), timeout=3.0)
        if len(p) < 4:
            return ""
        n = p[2]
        return p[3:3 + n].decode("ascii", errors="replace")

    async def read_support_info(self, sub: int, timeout: float = 3.0) -> bytes:
        await self.write(0x00, 0x02, bytes([sub]))
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            left = deadline - asyncio.get_event_loop().time()
            if left <= 0:
                raise asyncio.TimeoutError(f"timeout waiting for sub=0x{sub:02x}")
            op1, op2, p = await self.recv(timeout=left)
            if op1 != 0x00 or op2 != 0x02:
                continue
            if len(p) < 2 or p[1] != sub:
                continue
            return p

    async def read_support_info03(self, timeout: float = 3.0) -> SupportInfo03:
        p = await self.read_support_info(0x03, timeout=timeout)
        transfer_count = None
        print_count = None
        if len(p) >= 10:
            transfer_count = struct.unpack_from(">I", p, 2)[0]
            print_count = struct.unpack_from(">I", p, 6)[0]
        return SupportInfo03(raw=p, transfer_count=transfer_count, print_count=print_count)

    async def read_shot_counter(self, timeout: float = 3.0) -> Optional[int]:
        p = await self.read_support_info(0x05, timeout=timeout)
        if len(p) >= 6:
            return p[5]
        return None
