#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import time

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


def _is_jpeg_payload(p: bytes) -> bool:
    return len(p) > 5 and b"\xff\xd8" in p and b"\xff\xd9" in p


async def pull_once(cli: LinkClient, timeout: float = 2.5) -> tuple[bool, str]:
    await cli.write(0x82, 0x01)
    try:
        op1, op2, p = await cli.recv(timeout=timeout)
    except asyncio.TimeoutError:
        return False, "timeout"

    if op1 == 0x82 and op2 == 0x01:
        if _is_jpeg_payload(p):
            return True, f"frame ok ({len(p)}B payload)"
        return False, f"non-jpeg ({len(p)}B) {p[:12].hex()}"
    if op1 == 0x82 and op2 == 0x02:
        return False, f"camera closed live-view session payload={p.hex()}"
    return False, f"unexpected op=({op1:02x},{op2:02x}) payload={p[:12].hex()}"


async def read_reg(cli: LinkClient, reg_id: int, timeout: float = 1.5) -> tuple[bool, str]:
    await cli.flush()
    await cli.write(0x80, 0x11, bytes([reg_id, 0x00, 0x00, 0x00, 0x00, 0x00]))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            op1, op2, p = await cli.recv(timeout=0.25)
        except asyncio.TimeoutError:
            continue
        if op1 == 0x80 and op2 == 0x11:
            return True, p.hex()
    return False, "timeout"


async def write_reg(cli: LinkClient, reg_id: int, value: int, timeout: float = 1.5) -> tuple[bool, str]:
    await cli.flush()
    await cli.write(0x80, 0x11, bytes([reg_id, 0x02, value, 0x00, 0x00, 0x00]))
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        try:
            op1, op2, p = await cli.recv(timeout=0.25)
        except asyncio.TimeoutError:
            continue
        seen.append(f"({op1:02x},{op2:02x})/{p[:8].hex()}")
        if op1 == 0x80 and op2 == 0x11:
            return True, p.hex()
    if seen:
        return False, "timeout seen=" + ",".join(seen[:6])
    return False, "timeout"


async def main_async(address: str, warmup_pulls: int, timeout: float, include_candidates: bool) -> int:
    cli = LinkClient(address=address, verbose=True)
    try:
        await cli.connect()
        await cli.flush()
        await cli.hello()

        model = await cli.read_device_info(1)
        print(f"model={model}")

        print("opening live-view session (82,00)...")
        op1, op2, p = await cli.exchange(0x82, 0x00, b"\x00", timeout=5.0)
        print(f"open ack: op=({op1:02x},{op2:02x}) payload={p.hex()}")

        print(f"warm-up pulls: {warmup_pulls}")
        ok_frames = 0
        for i in range(warmup_pulls):
            ok, msg = await pull_once(cli, timeout=timeout)
            if ok:
                ok_frames += 1
            print(f"  warmup {i + 1}/{warmup_pulls}: {msg}")

        print("\n=== Flash register trial in active live-view ===")
        trials: list[tuple[str, int, int]] = [
            ("reg0B=ON", 0x0B, 0x01),
            ("reg0B=OFF", 0x0B, 0x02),
            ("reg0B=AUTO", 0x0B, 0x00),
        ]
        if include_candidates:
            trials.extend(
                [
                    ("reg8D=05", 0x8D, 0x05),
                    ("reg8E=0D", 0x8E, 0x0D),
                ]
            )

        for label, reg, val in trials:
            r_ok, r_msg = await read_reg(cli, reg, timeout=1.2)
            print(f"read before {label}: reg=0x{reg:02x} -> {('ok ' + r_msg) if r_ok else ('no-reply ' + r_msg)}")

            w_ok, w_msg = await write_reg(cli, reg, val, timeout=1.5)
            print(f"write {label}: {('ack ' + w_msg) if w_ok else ('no-ack ' + w_msg)}")

            rb_ok, rb_msg = await read_reg(cli, reg, timeout=1.2)
            print(f"read after  {label}: reg=0x{reg:02x} -> {('ok ' + rb_msg) if rb_ok else ('no-reply ' + rb_msg)}")

            ok, msg = await pull_once(cli, timeout=timeout)
            print(f"post-trial pull: {msg}")
            if not ok and "camera closed" in msg:
                print("live-view closed by camera during trial")
                break

        print("\nclosing live-view session (82,02)...")
        try:
            op1, op2, p = await cli.exchange(0x82, 0x02, b"\x00", timeout=3.0)
            print(f"close ack: op=({op1:02x},{op2:02x}) payload={p.hex()}")
        except Exception as e:
            print(f"close ack missing: {e}")

        print("\n=== Summary ===")
        print(f"warm-up valid frames: {ok_frames}/{warmup_pulls}")
        print("done")
        return 0
    finally:
        await cli.disconnect()


def main() -> int:
    ap = argparse.ArgumentParser(description="FI019 flash command trial during active live-view")
    ap.add_argument("--address", default=MINI_EVO_ADDR)
    ap.add_argument("--warmup-pulls", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument(
        "--include-candidates",
        action="store_true",
        help="Also test Android-inferred candidates reg 0x8D/0x8E",
    )
    args = ap.parse_args()
    return asyncio.run(
        main_async(
            address=args.address,
            warmup_pulls=args.warmup_pulls,
            timeout=args.timeout,
            include_candidates=args.include_candidates,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
