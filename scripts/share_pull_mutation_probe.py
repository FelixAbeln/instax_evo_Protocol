#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import struct
from datetime import datetime
from pathlib import Path

from instax_lab.protocol import DEFAULT_ADDR, MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


class Trace:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def note(self, text: str) -> None:
        self.lines.append(f"# {text}")

    def tx(self, op1: int, op2: int, payload: bytes = b"") -> None:
        self.lines.append(f"TX {op1:02x} {op2:02x} {payload.hex()}")

    def rx(self, op1: int, op2: int, payload: bytes = b"") -> None:
        self.lines.append(f"RX {op1:02x} {op2:02x} {payload.hex()}")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _print_tx(op1: int, op2: int, payload: bytes) -> None:
    print(f"TX ({op1:02x},{op2:02x}) len={len(payload)} payload={payload.hex()}")


def _print_rx(op1: int, op2: int, payload: bytes, note: str = "") -> None:
    suffix = f" {note}" if note else ""
    print(f"RX ({op1:02x},{op2:02x}) len={len(payload)} payload={payload.hex()}{suffix}")


async def exchange(cli: LinkClient, tr: Trace, op1: int, op2: int, payload: bytes = b"", timeout: float = 5.0) -> tuple[int, int, bytes]:
    tr.tx(op1, op2, payload)
    _print_tx(op1, op2, payload)
    rop1, rop2, rp = await cli.exchange(op1, op2, payload, timeout=timeout)
    tr.rx(rop1, rop2, rp)
    _print_rx(rop1, rop2, rp)
    return rop1, rop2, rp


async def send_only(cli: LinkClient, tr: Trace, op1: int, op2: int, payload: bytes = b"") -> None:
    tr.tx(op1, op2, payload)
    _print_tx(op1, op2, payload)
    await cli.write(op1, op2, payload)


async def recv_match(
    cli: LinkClient,
    tr: Trace,
    want_op1: int,
    want_op2: int,
    timeout: float,
    payload_predicate=None,
) -> tuple[int, int, bytes]:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise asyncio.TimeoutError(
                f"timeout waiting for ({want_op1:02x},{want_op2:02x})"
            )
        rop1, rop2, rp = await cli.recv(timeout=left)
        tr.rx(rop1, rop2, rp)
        note = ""
        if (rop1, rop2) != (want_op1, want_op2):
            note = f"[unexpected, waiting for ({want_op1:02x},{want_op2:02x})]"
            _print_rx(rop1, rop2, rp, note)
            continue
        if payload_predicate is not None and not payload_predicate(rp):
            note = "[unexpected payload]"
            _print_rx(rop1, rop2, rp, note)
            continue
        _print_rx(rop1, rop2, rp, "[match]")
        return rop1, rop2, rp


async def read_device_info_logged(cli: LinkClient, tr: Trace, info_type: int, timeout: float = 3.0) -> str:
    await send_only(cli, tr, 0x00, 0x01, bytes([info_type]))
    _op1, _op2, p = await recv_match(
        cli,
        tr,
        0x00,
        0x01,
        timeout=timeout,
        payload_predicate=lambda x: len(x) >= 2 and x[1] == info_type,
    )
    if len(p) < 4:
        return ""
    n = p[2]
    return p[3:3 + n].decode("ascii", errors="replace")


async def wait_share_ready(cli: LinkClient, tr: Trace, timeout: float, poll_delay: float) -> tuple[int, bytes]:
    deadline = asyncio.get_event_loop().time() + timeout
    last = b""
    while True:
        await send_only(cli, tr, 0x00, 0x02, b"\x04")
        _op1, _op2, p = await recv_match(
            cli,
            tr,
            0x00,
            0x02,
            timeout=3.0,
            payload_predicate=lambda x: len(x) >= 2 and x[1] == 0x04,
        )
        last = p
        ready = p[4] if len(p) > 4 else 0
        q_like = p[5] if len(p) > 5 else 0
        print(f"info04 decode ready=0x{ready:02x} q_like={q_like}")
        if ready != 0:
            return ready, p
        if asyncio.get_event_loop().time() >= deadline:
            return 0, p
        await asyncio.sleep(poll_delay)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Simple share-pull mutation probe. "
            "Default flow: wait ready flag -> (88,00) -> optional (88,01) -> N*(88,02) -> optional close."
        )
    )
    p.add_argument("--address", default=MINI_EVO_ADDR, help="Camera BLE address")
    p.add_argument("--wide-default", action="store_true", help="Use FI028 default address when --address is not set")
    p.add_argument("--verbose", action="store_true", help="Verbose LinkClient logs")

    p.add_argument("--wait-share", type=float, default=30.0, help="Seconds to wait for info04 ready flag")
    p.add_argument("--poll-delay", type=float, default=0.5, help="Delay between info04 polls")
    p.add_argument("--skip-wait-flag", action="store_true", help="Do not wait for info04 ready flag")

    p.add_argument("--prep-85", action="store_true", help="Run 85,00 -> 85,01 -> 85,00 before pull start")
    p.add_argument("--post-flag-delay", type=float, default=0.0, help="Sleep this many seconds after ready flag")

    p.add_argument("--skip-88-01", action="store_true", help="Skip metadata request (88,01)")
    p.add_argument(
        "--meta-payload-hex",
        default="00000000",
        help="Hex payload for 88,01 when enabled (default 00000000)",
    )

    p.add_argument("--chunks", type=int, default=1, help="How many 88,02 chunk requests to send")
    p.add_argument("--start-chunk", type=int, default=0, help="Start chunk index for 88,02")
    p.add_argument("--chunk-timeout", type=float, default=8.0, help="Per-chunk receive timeout")

    p.add_argument("--skip-close", action="store_true", help="Skip 88,03 and 88,05 close packets")

    p.add_argument("--out-dir", type=Path, default=Path("captures/image_transfer"), help="Output folder for trace/jpg")
    p.add_argument("--tag", default="", help="Tag added to output filenames")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    address = args.address
    if args.wide_default and (not args.address or args.address == MINI_EVO_ADDR):
        address = DEFAULT_ADDR

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / f"share_mutation{tag}_{ts}.trace"

    cli = LinkClient(address=address, verbose=args.verbose)
    tr = Trace()
    jpeg_buf = bytearray()

    try:
        await cli.connect()
        await cli.flush()

        # Keep setup minimal but deterministic.
        try:
            await exchange(cli, tr, 0x00, 0x00, timeout=3.0)
        except Exception as e:
            tr.note(f"hello_nonfatal={type(e).__name__}:{e}")

        model = ""
        serial = ""
        try:
            model = await read_device_info_logged(cli, tr, 1)
            serial = await read_device_info_logged(cli, tr, 2)
        except Exception:
            pass
        print(f"connected model={model or '?'} serial={serial or '?'} addr={address}")
        tr.note(f"model={model or '?'} serial={serial or '?'} addr={address}")

        if args.prep_85:
            print("prep flow: 85,00 -> 85,01 -> 85,00")
            await exchange(cli, tr, 0x85, 0x00, timeout=3.0)
            await exchange(cli, tr, 0x85, 0x01, bytes.fromhex("05" + "00" * 8), timeout=3.0)
            await exchange(cli, tr, 0x85, 0x00, timeout=3.0)

        if not args.skip_wait_flag:
            print("press Share now, waiting for info04 ready flag...")
            ready, raw = await wait_share_ready(cli, tr, timeout=args.wait_share, poll_delay=args.poll_delay)
            tr.note(f"ready_flag=0x{ready:02x} info04={raw.hex()}")
            if ready == 0:
                print("ready flag did not appear")
                return 2

        if args.post_flag_delay > 0:
            print(f"sleeping after ready flag: {args.post_flag_delay:.2f}s")
            await asyncio.sleep(args.post_flag_delay)

        print("sending 88,00")
        op1, op2, p = await exchange(cli, tr, 0x88, 0x00, timeout=6.0)
        print(f"88,00 <- ({op1:02x},{op2:02x}) {p.hex()}")

        if not args.skip_88_01:
            meta_payload = bytes.fromhex(args.meta_payload_hex)
            print(f"sending 88,01 payload={meta_payload.hex()}")
            op1, op2, p = await exchange(cli, tr, 0x88, 0x01, meta_payload, timeout=6.0)
            print(f"88,01 <- ({op1:02x},{op2:02x}) len={len(p)} payload={p.hex()}")
            if len(p) >= 10:
                total = struct.unpack_from(">I", p, 1)[0]
                chunk = struct.unpack_from(">I", p, 5)[0]
                print(f"metadata decode: total={total} chunk={chunk}")

        print(f"chunk loop: count={args.chunks} start={args.start_chunk}")
        for i in range(args.chunks):
            idx = args.start_chunk + i
            payload = idx.to_bytes(4, "big")
            await send_only(cli, tr, 0x88, 0x02, payload)
            try:
                rop1, rop2, rp = await cli.recv(timeout=args.chunk_timeout)
            except asyncio.TimeoutError:
                tr.note(f"chunk_timeout idx={idx}")
                print(f"chunk {idx}: timeout")
                break
            tr.rx(rop1, rop2, rp)
            _print_rx(rop1, rop2, rp, f"[chunk idx={idx}]")
            if rop1 == 0x88 and rop2 == 0x02 and len(rp) >= 5:
                jpeg_buf.extend(rp[5:])

        if not args.skip_close:
            print("closing: 88,03 then 88,05")
            try:
                await exchange(cli, tr, 0x88, 0x03, timeout=3.0)
            except Exception as e:
                tr.note(f"close_8803_failed={type(e).__name__}:{e}")
            try:
                await exchange(cli, tr, 0x88, 0x05, b"\x00\x00\x00\x00", timeout=3.0)
            except Exception as e:
                tr.note(f"close_8805_failed={type(e).__name__}:{e}")

        data = bytes(jpeg_buf)
        soi = data.find(b"\xff\xd8")
        eoi = data.rfind(b"\xff\xd9")
        if soi >= 0 and eoi > soi:
            jpg = data[soi:eoi + 2]
            jpg_path = out_dir / f"share_mutation{tag}_{model or 'unknown'}_{ts}.jpg"
            jpg_path.write_bytes(jpg)
            tr.note(f"saved_jpeg={jpg_path} bytes={len(jpg)}")
            print(f"saved jpeg {len(jpg)} B -> {jpg_path}")
        else:
            tr.note(f"jpeg_incomplete buffer={len(data)}")
            print(f"jpeg incomplete buffer={len(data)} B")

        return 0

    except Exception as e:
        tr.note(f"probe_failed={type(e).__name__}:{e}")
        print(f"probe failed/disconnected: {type(e).__name__}: {e}")
        return 10
    finally:
        try:
            tr.save(trace_path)
            print(f"trace saved -> {trace_path}")
        except Exception as e:
            print(f"trace save failed: {e}")
        await cli.disconnect()


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
