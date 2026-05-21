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
        description="Live watch (00,02) support-info payloads and print byte-level changes"
    )
    p.add_argument("--address", default=MINI_EVO_ADDR, help="Camera BLE address")
    p.add_argument("--poll-delay", type=float, default=0.4, help="Delay between full poll cycles")
    p.add_argument("--duration", type=float, default=180.0, help="Max run seconds")
    p.add_argument("--out", type=Path, default=None, help="Optional log output path")
    p.add_argument("--verbose", action="store_true", help="Verbose BLE logs")
    return p.parse_args()


def hex_bytes(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def diff_bytes(old: bytes, new: bytes) -> str:
    n = min(len(old), len(new))
    parts: list[str] = []
    for i in range(n):
        if old[i] != new[i]:
            parts.append(f"[{i}] {old[i]:02x}->{new[i]:02x}")
    if len(old) != len(new):
        parts.append(f"len {len(old)}->{len(new)}")
    return ", ".join(parts) if parts else "no-byte-change"


async def main_async(args: argparse.Namespace) -> int:
    cli = LinkClient(address=args.address, verbose=args.verbose)
    start_ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_path = args.out or Path("captures") / f"share_flag_watch_{start_ts}.log"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    # Keep poll order aligned with app keepalive.
    subs = [0x02, 0x03, 0x01, 0x04, 0x05]
    last: dict[int, bytes] = {}

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
        log("watching support-info subs: 02,03,01,04,05")
        log("press Share on camera now; watcher prints every response and highlights deltas")

        t_end = asyncio.get_event_loop().time() + args.duration
        cycle = 0
        while asyncio.get_event_loop().time() < t_end:
            cycle += 1
            for sub in subs:
                try:
                    p = await cli.read_support_info(sub, timeout=3.0)
                except Exception as e:
                    log(f"cycle={cycle:04d} sub={sub:02x} ERROR {type(e).__name__}: {e}")
                    continue

                row = f"cycle={cycle:04d} sub={sub:02x} len={len(p):02d} raw={hex_bytes(p)}"
                if sub not in last:
                    log(row + "  [first]")
                else:
                    d = diff_bytes(last[sub], p)
                    if d == "no-byte-change":
                        log(row)
                    else:
                        log(row + f"  [CHANGED {d}]")
                last[sub] = p

            await asyncio.sleep(args.poll_delay)

        log("watch complete")
        return 0
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
