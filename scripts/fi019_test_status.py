#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import struct

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


async def main_async(address: str) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.flush()
        await cli.hello()

        maker = await cli.read_device_info(0x00)
        model = await cli.read_device_info(0x01)
        serial = await cli.read_device_info(0x02)

        p0 = await cli.read_support_info(0x00)
        dims = "unknown"
        if len(p0) >= 6:
            w, h = struct.unpack_from(">HH", p0, 2)
            dims = f"{w}x{h}"

        p1 = await cli.read_support_info(0x01)
        battery_state = p1[2] if len(p1) >= 3 else None
        battery_pct = p1[3] if len(p1) >= 4 else None

        p2 = await cli.read_support_info(0x02)
        photos_left = (p2[2] & 0x0F) if len(p2) >= 3 else None

        p3 = await cli.read_support_info03()
        p4 = await cli.read_support_info(0x04)
        p5 = await cli.read_shot_counter()

        print("\n=== FI019 status probe ===")
        print(f"address:      {address}")
        print(f"manufacturer: {maker}")
        print(f"model:        {model}")
        print(f"serial:       {serial}")
        print(f"image_size:   {dims}")
        print(f"battery:      state={battery_state} pct={battery_pct}")
        print(f"photos_left:  {photos_left}")
        print(f"info03:       transfers={p3.transfer_count} prints={p3.print_count} raw={p3.raw.hex()}")
        print(f"info04:       raw={p4.hex()}")
        print(f"info05:       shot_counter={p5}")
        print("===========================")
        return 0
    finally:
        await cli.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="FI019 baseline status probe")
    ap.add_argument("--address", default=MINI_EVO_ADDR)
    args = ap.parse_args()
    return asyncio.run(main_async(args.address))


if __name__ == "__main__":
    raise SystemExit(main())
