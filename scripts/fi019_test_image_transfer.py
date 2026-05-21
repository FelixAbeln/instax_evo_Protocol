#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


async def wait_for_share_flag(cli: LinkClient, timeout: float) -> int:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        p4 = await cli.read_support_info(0x04, timeout=3.0)
        flag = p4[4] if len(p4) > 4 else 0
        print(f"info04 raw={p4.hex()} flag=0x{flag:02x}")
        if flag != 0:
            return flag
        if asyncio.get_event_loop().time() >= deadline:
            return 0
        await asyncio.sleep(0.5)


async def main_async(address: str, wait_share: float) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.flush()
        await cli.hello()

        print("\nPress Share on the camera now to enter transfer-ready mode.")
        flag = await wait_for_share_flag(cli, wait_share)
        if flag == 0:
            print("transfer-ready flag never appeared; skipping (88,00) test")
            return 1

        print("trying IMAGE_TRANSFER_START (88,00)...")
        try:
            op1, op2, p = await cli.exchange(0x88, 0x00, timeout=6.0)
            print(f"(88,00) response op=({op1:02x},{op2:02x}) payload={p.hex()}")
            if op1 == 0x88 and op2 == 0x00 and len(p) >= 1 and p[0] == 0x00:
                print("result: (88,xx) appears supported on this FI019")
                return 0
            print("result: (88,xx) not confirmed")
            return 2
        except Exception as e:
            print(f"(88,00) failed/disconnected: {e}")
            print("result: (88,xx) likely unsupported on this FI019")
            return 3
    finally:
        await cli.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="FI019 image transfer probe (Share + 88,xx)")
    ap.add_argument("--address", default=MINI_EVO_ADDR)
    ap.add_argument("--wait-share", type=float, default=30.0)
    args = ap.parse_args()
    return asyncio.run(main_async(args.address, args.wait_share))


if __name__ == "__main__":
    raise SystemExit(main())
