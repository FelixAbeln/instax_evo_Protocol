#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import time

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


async def main_async(address: str, seconds: float, interval: float) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.flush()
        await cli.hello()

        print("\n=== FI019 counter probe ===")
        print("watching support-info sub=0x03 (transfer/print) and sub=0x05 (shots)")

        t0 = time.time()
        last_print = None
        last_transfer = None
        last_shot = None

        while time.time() - t0 < seconds:
            p3 = await cli.read_support_info03(timeout=3.0)
            shot = await cli.read_shot_counter(timeout=3.0)

            dt = time.time() - t0
            transfer = p3.transfer_count
            prints = p3.print_count

            changed = []
            if transfer != last_transfer:
                changed.append(f"transfer={transfer}")
            if prints != last_print:
                changed.append(f"prints={prints}")
            if shot != last_shot:
                changed.append(f"shots={shot}")

            line = (
                f"t={dt:5.1f}s transfer={transfer} prints={prints} shots={shot} "
                f"raw03={p3.raw.hex()}"
            )
            if changed:
                line += "  CHANGED: " + ", ".join(changed)
            print(line)

            last_transfer = transfer
            last_print = prints
            last_shot = shot
            await asyncio.sleep(interval)

        print("\nsummary:")
        print(f"final transfer_count={last_transfer}")
        print(f"final print_count={last_print}")
        print(f"final shot_counter={last_shot}")
        return 0
    finally:
        await cli.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="FI019 lifetime counter probe")
    ap.add_argument("--address", default=MINI_EVO_ADDR)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    return asyncio.run(main_async(args.address, args.seconds, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
