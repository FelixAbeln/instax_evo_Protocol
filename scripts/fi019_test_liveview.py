#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


async def main_async(address: str, pulls: int, timeout: float) -> int:
    cli = LinkClient(address=address, verbose=True)
    frame_ok = 0
    frame_bad = 0

    try:
        await cli.connect()
        await cli.flush()
        await cli.hello()

        print("opening live-view session (82,00)...")
        op1, op2, p = await cli.exchange(0x82, 0x00, b"\x00", timeout=5.0)
        print(f"open ack: op=({op1:02x},{op2:02x}) payload={p.hex()}")

        for i in range(pulls):
            await cli.write(0x82, 0x01)
            try:
                op1, op2, p = await cli.recv(timeout=timeout)
            except asyncio.TimeoutError:
                print(f"pull {i + 1}/{pulls}: timeout")
                break

            if op1 == 0x82 and op2 == 0x01:
                if len(p) > 5 and b"\xff\xd8" in p and b"\xff\xd9" in p:
                    frame_ok += 1
                    print(f"pull {i + 1}/{pulls}: frame ok ({len(p)}B payload)")
                else:
                    frame_bad += 1
                    print(f"pull {i + 1}/{pulls}: non-jpeg payload ({len(p)}B) {p[:16].hex()}")
            elif op1 == 0x82 and op2 == 0x02:
                print(f"pull {i + 1}/{pulls}: camera closed session payload={p.hex()}")
                break
            else:
                print(f"pull {i + 1}/{pulls}: unexpected op=({op1:02x},{op2:02x}) payload={p.hex()}")

        print("closing live-view session (82,02)...")
        try:
            op1, op2, p = await cli.exchange(0x82, 0x02, b"\x00", timeout=3.0)
            print(f"close ack: op=({op1:02x},{op2:02x}) payload={p.hex()}")
        except Exception as e:
            print(f"close ack missing: {e}")

        print("\n=== FI019 live-view probe summary ===")
        print(f"valid jpeg frames: {frame_ok}")
        print(f"non-jpeg frames:   {frame_bad}")
        if frame_ok > 0:
            print("result: LIVE VIEW looks supported")
            return 0
        print("result: LIVE VIEW not confirmed")
        return 2
    finally:
        await cli.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="FI019 live-view protocol probe (82,xx)")
    ap.add_argument("--address", default=MINI_EVO_ADDR)
    ap.add_argument("--pulls", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=5.0)
    args = ap.parse_args()
    return asyncio.run(main_async(args.address, args.pulls, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
