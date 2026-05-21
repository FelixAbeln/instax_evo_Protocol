from __future__ import annotations

import argparse
import asyncio
from typing import Iterable

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


def parse_ids(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if "-" in part:
            a_s, b_s = part.split("-", 1)
            a = int(a_s, 16)
            b = int(b_s, 16)
            step = 1 if b >= a else -1
            out.extend(list(range(a, b + step, step)))
        else:
            out.append(int(part, 16))
    # Keep order while deduping.
    seen: set[int] = set()
    dedup: list[int] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            dedup.append(x)
    return [x & 0xFF for x in dedup]


def parse_writes(spec: str) -> list[tuple[int, int]]:
    items: list[tuple[int, int]] = []
    if not spec.strip():
        return items
    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue
        reg_s, val_s = part.split(":", 1)
        reg = int(reg_s, 16) & 0xFF
        val = int(val_s, 16) & 0xFF
        items.append((reg, val))
    return items


async def recv_80_11(cli: LinkClient, timeout: float) -> tuple[int, int, bytes]:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise asyncio.TimeoutError("timeout waiting for (80,11)")
        op1, op2, payload = await cli.recv(timeout=left)
        if op1 == 0x80 and op2 == 0x11:
            return op1, op2, payload


async def read_reg(cli: LinkClient, reg: int, timeout: float) -> bytes:
    await cli.flush()
    await cli.write(0x80, 0x11, bytes([reg, 0x00, 0x00, 0x00, 0x00, 0x00]))
    _o1, _o2, p = await recv_80_11(cli, timeout)
    return p


async def write_reg(cli: LinkClient, reg: int, value: int, timeout: float) -> bytes:
    await cli.flush()
    await cli.write(0x80, 0x11, bytes([reg, 0x02, value, 0x00, 0x00, 0x00]))
    _o1, _o2, p = await recv_80_11(cli, timeout)
    return p


async def run_probe(
    address: str,
    ids: Iterable[int],
    timeout: float,
    write_pairs: list[tuple[int, int]],
    settle_ms: int,
) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.hello()
        model = await cli.read_device_info(1)
        print(f"model={model}")

        print("\n=== READ PROBE (0x80,0x11) ===")
        responsive: dict[int, bytes] = {}
        for reg in ids:
            try:
                p = await read_reg(cli, reg, timeout)
                responsive[reg] = p
                print(f"reg 0x{reg:02x}: reply len={len(p)} payload={p.hex()}")
            except asyncio.TimeoutError:
                print(f"reg 0x{reg:02x}: timeout")
            await asyncio.sleep(settle_ms / 1000.0)

        if not write_pairs:
            print("\nNo writes requested (safe read-only mode).")
            print(f"Responsive regs: {', '.join(f'0x{x:02x}' for x in responsive) or 'none'}")
            return 0

        print("\n=== WRITE PROBE (explicit opt-in) ===")
        for reg, value in write_pairs:
            if reg not in responsive:
                print(
                    f"skip write reg 0x{reg:02x}: no read response in this session"
                )
                continue
            try:
                before = responsive[reg]
                ack = await write_reg(cli, reg, value, timeout)
                print(
                    f"write reg 0x{reg:02x}=0x{value:02x}: "
                    f"ack len={len(ack)} payload={ack.hex()}"
                )
                await asyncio.sleep(max(settle_ms, 300) / 1000.0)
                after = await read_reg(cli, reg, timeout)
                print(f"readback reg 0x{reg:02x}: {after.hex()} (before={before.hex()})")
            except Exception as e:
                print(f"write reg 0x{reg:02x}=0x{value:02x} failed: {e!r}")
            await asyncio.sleep(settle_ms / 1000.0)

        return 0
    finally:
        await cli.disconnect()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Conservative Link-profile register probe for FI019"
    )
    p.add_argument("--address", default=MINI_EVO_ADDR)
    p.add_argument(
        "--ids",
        default="0b,80-8f",
        help="Hex IDs to read, e.g. 0b,80-8f,17",
    )
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument(
        "--write",
        default="",
        help="Optional writes reg:val pairs, e.g. 8d:05,8e:0d",
    )
    p.add_argument(
        "--settle-ms",
        type=int,
        default=250,
        help="Delay between operations",
    )
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    ids = parse_ids(args.ids)
    writes = parse_writes(args.write)
    return asyncio.run(
        run_probe(
            address=args.address,
            ids=ids,
            timeout=args.timeout,
            write_pairs=writes,
            settle_ms=max(0, args.settle_ms),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
