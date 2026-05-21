#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Watch InfoType=0x04 and stop when queue-like byte increments"
    )
    p.add_argument("--address", default=MINI_EVO_ADDR, help="Camera BLE address")
    p.add_argument("--timeout", type=float, default=120.0, help="Max seconds to wait for increment")
    p.add_argument("--poll-delay", type=float, default=0.4, help="Delay between polls")
    p.add_argument("--arm-delay", type=float, default=15.0, help="Seconds to wait before polling starts")
    p.add_argument("--verbose", action="store_true", help="Verbose LinkClient logs")
    p.add_argument("--out", type=Path, default=None, help="Optional output log path")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    cli = LinkClient(address=args.address, verbose=args.verbose)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = args.out or Path("captures") / f"queue_increment_watch_{ts}.log"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    try:
        await cli.connect()
        await cli.flush()
        try:
            await cli.hello()
        except Exception:
            pass

        model = "?"
        serial = "?"
        try:
            model = await cli.read_device_info(1) or "?"
        except Exception:
            pass
        try:
            serial = await cli.read_device_info(2) or "?"
        except Exception:
            pass

        log(f"connected model={model} serial={serial} address={args.address}")
        log("watch field: InfoType=0x04 payload[4]=ready, payload[5]=queue_like")

        if args.arm_delay > 0:
            log(f"arming delay: {args.arm_delay:.1f}s (press Share now)")
            await asyncio.sleep(args.arm_delay)

        p0 = await cli.read_support_info(0x04, timeout=3.0)
        baseline_ready = p0[4] if len(p0) > 4 else 0
        baseline_q = p0[5] if len(p0) > 5 else 0
        log(f"baseline raw={p0.hex()} ready=0x{baseline_ready:02x} q_like={baseline_q}")

        deadline = asyncio.get_event_loop().time() + args.timeout
        poll_n = 0
        while asyncio.get_event_loop().time() < deadline:
            poll_n += 1
            p = await cli.read_support_info(0x04, timeout=3.0)
            ready = p[4] if len(p) > 4 else 0
            q_like = p[5] if len(p) > 5 else 0
            log(f"poll={poll_n:03d} raw={p.hex()} ready=0x{ready:02x} q_like={q_like}")

            if q_like > baseline_q:
                log(f"INCREMENT DETECTED: q_like {baseline_q} -> {q_like}")
                return 0

            await asyncio.sleep(args.poll_delay)

        log("timeout: no queue increment observed")
        return 1
    finally:
        try:
            out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"log saved -> {out_path}")
        except Exception as e:
            print(f"log save failed: {e}")
        await cli.disconnect()


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
