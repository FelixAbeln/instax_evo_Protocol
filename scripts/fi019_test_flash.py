from __future__ import annotations

import argparse
import asyncio

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe FI019 flash register write/read behavior")
    p.add_argument("--address", default=MINI_EVO_ADDR, help="Camera BLE address")
    p.add_argument("--timeout", type=float, default=3.0, help="Per-response timeout seconds")
    return p.parse_args()


async def recv_80_11(cli: LinkClient, timeout: float) -> bytes:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise asyncio.TimeoutError("timeout waiting for (80,11)")
        op1, op2, payload = await cli.recv(timeout=left)
        if op1 == 0x80 and op2 == 0x11:
            return payload


async def recv_op(cli: LinkClient, op1_want: int, op2_want: int, timeout: float) -> bytes:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise asyncio.TimeoutError(f"timeout waiting for ({op1_want:02x},{op2_want:02x})")
        op1, op2, payload = await cli.recv(timeout=left)
        if op1 == op1_want and op2 == op2_want:
            return payload


async def read_reg(cli: LinkClient, reg_id: int, timeout: float) -> bytes:
    await cli.flush()
    await cli.write(0x80, 0x11, bytes([reg_id, 0x00, 0x00, 0x00, 0x00, 0x00]))
    return await recv_80_11(cli, timeout)


async def write_reg(cli: LinkClient, reg_id: int, value: int, timeout: float) -> bytes:
    await cli.flush()
    await cli.write(0x80, 0x11, bytes([reg_id, 0x02, value, 0x00, 0x00, 0x00]))
    return await recv_80_11(cli, timeout)


async def main_async(address: str, timeout: float) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.hello()

        model = await cli.read_device_info(1)
        print(f"model={model}")

        reg_id = 0x0B
        names = {0: "AUTO", 1: "ON", 2: "OFF"}

        async def probe_once(label: str) -> bool:
            print(f"-- {label} --")
            try:
                before = await read_reg(cli, reg_id, timeout)
                print(f"read before: {before.hex()} (len={len(before)})")
            except Exception as e:
                print(f"read before failed: {e}")
                return False

            for value in (1, 2, 0):
                try:
                    ack = await write_reg(cli, reg_id, value, timeout)
                    print(f"write {names[value]} ack: {ack.hex()} (len={len(ack)})")
                except Exception as e:
                    print(f"write {names[value]} failed: {e}")
                    continue

                try:
                    after = await read_reg(cli, reg_id, timeout)
                    print(f"read after {names[value]}: {after.hex()} (len={len(after)})")
                except Exception as e:
                    print(f"read after {names[value]} failed: {e}")
            return True

        ok_idle = await probe_once("idle")

        # Some models may only expose control registers while LV is active.
        await cli.flush()
        await cli.write(0x82, 0x00, b"\x00")
        try:
            ack_lv = await recv_op(cli, 0x82, 0x00, timeout)
            print(f"live-view open ack: {ack_lv.hex()} (len={len(ack_lv)})")
        except Exception as e:
            print(f"live-view open failed: {e}")
            ack_lv = None

        ok_live = False
        if ack_lv is not None:
            ok_live = await probe_once("live-view-open")
            await cli.flush()
            await cli.write(0x82, 0x02, b"\x00")
            try:
                ack_close = await recv_op(cli, 0x82, 0x02, timeout)
                print(f"live-view close ack: {ack_close.hex()} (len={len(ack_close)})")
            except Exception as e:
                print(f"live-view close ack missing: {e}")
        if not ok_idle and not ok_live:
            print("result: register 0x0B access unavailable on FI019 in this firmware/session")
            return 2

        print("done")
        return 0
    finally:
        await cli.disconnect()


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args.address, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
