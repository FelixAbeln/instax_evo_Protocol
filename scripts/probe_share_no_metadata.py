#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

from instax_lab.protocol import DEFAULT_ADDR, MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


class TraceRecorder:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def tx(self, op1: int, op2: int, payload: bytes) -> None:
        self._lines.append(f"TX {op1:02x} {op2:02x} {payload.hex()}")

    def rx(self, op1: int, op2: int, payload: bytes) -> None:
        self._lines.append(f"RX {op1:02x} {op2:02x} {payload.hex()}")

    def note(self, text: str) -> None:
        self._lines.append(f"# {text}")

    def save(self, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Probe Share transfer without requesting metadata (skip 0x88,01). "
            "Flow: 85,00 -> 85,01 -> 85,00 -> wait InfoType=0x04 flag -> 88,00 -> 88,02..."
        )
    )
    p.add_argument("--address", default=MINI_EVO_ADDR, help="Camera BLE address")
    p.add_argument("--wait-share", type=float, default=30.0, help="Seconds to wait for transfer-ready flag")
    p.add_argument("--poll-delay", type=float, default=0.5, help="Delay between InfoType=0x04 polls")
    p.add_argument(
        "--arm-delay",
        type=float,
        default=0.0,
        help="Seconds to wait after connect before starting flag polling",
    )
    p.add_argument(
        "--require-flag-hits",
        type=int,
        default=2,
        help="Require this many consecutive non-zero share-flag polls before pull start",
    )
    p.add_argument(
        "--post-flag-delay",
        type=float,
        default=0.7,
        help="Seconds to wait after stable flag before sending pull start",
    )
    p.add_argument(
        "--min-queue-byte",
        type=int,
        default=0,
        help=(
            "Require InfoType=0x04 payload[5] >= this value before pull start "
            "(FI019 observed as queue-like byte; 0 disables this gate)"
        ),
    )
    p.add_argument(
        "--require-edge",
        action="store_true",
        help=(
            "Require a new transition after watcher starts: ready 0->nonzero "
            "or queue-like byte increase"
        ),
    )
    p.add_argument("--max-chunks", type=int, default=40, help="Maximum 0x88,02 chunk requests")
    p.add_argument("--chunk-timeout", type=float, default=8.0, help="Per-chunk receive timeout")
    p.add_argument("--empty-stop", type=int, default=2, help="Stop after N consecutive empty chunk payloads")
    p.add_argument("--out-dir", type=Path, default=Path("captures/image_transfer"), help="JPEG output directory")
    p.add_argument("--verbose", action="store_true", help="Verbose LinkClient logs")
    p.add_argument(
        "--trace-out",
        type=Path,
        default=None,
        help="Write TX/RX trace to this file (default: out-dir/share_no_meta_<ts>.trace)",
    )
    p.add_argument(
        "--skip-85-prepare",
        action="store_true",
        help="Skip 85,xx transfer-mode prepare (only poll share flag + run 88,xx)",
    )
    p.add_argument(
        "--wide-default",
        action="store_true",
        help="Use FI028 default address if --address is not set",
    )
    return p.parse_args()


async def exchange_traced(
    cli: LinkClient,
    trace: Optional[TraceRecorder],
    op1: int,
    op2: int,
    payload: bytes = b"",
    timeout: float = 5.0,
) -> tuple[int, int, bytes]:
    if trace is not None:
        trace.tx(op1, op2, payload)
    rop1, rop2, rp = await cli.exchange(op1, op2, payload, timeout=timeout)
    if trace is not None:
        trace.rx(rop1, rop2, rp)
    return rop1, rop2, rp


async def read_device_info_safe(cli: LinkClient, info_type: int) -> str:
    try:
        return await cli.read_device_info(info_type)
    except Exception:
        return ""


async def wait_share_flag(cli: LinkClient, timeout: float, poll_delay: float) -> int:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        p4 = await cli.read_support_info(0x04, timeout=3.0)
        flag = p4[4] if len(p4) > 4 else 0
        print(f"info04 raw={p4.hex()} flag=0x{flag:02x}")
        if flag != 0:
            return flag
        if asyncio.get_event_loop().time() >= deadline:
            return 0
        await asyncio.sleep(poll_delay)


async def wait_share_flag_stable(
    cli: LinkClient,
    timeout: float,
    poll_delay: float,
    require_hits: int,
    min_queue_byte: int,
    require_edge: bool,
) -> tuple[int, int]:
    """Wait until ready/queue criteria are stable for N consecutive polls.

    ready criterion: InfoType=0x04 payload[4] != 0
    queue criterion: payload[5] >= min_queue_byte (if min_queue_byte > 0)
    """
    deadline = asyncio.get_event_loop().time() + timeout
    consec = 0
    last_flag = 0
    last_q = 0
    baseline_flag = None
    baseline_q = None
    edge_seen = not require_edge

    while True:
        p4 = await cli.read_support_info(0x04, timeout=3.0)
        flag = p4[4] if len(p4) > 4 else 0
        q_like = p4[5] if len(p4) > 5 else 0
        if baseline_flag is None:
            baseline_flag = flag
            baseline_q = q_like
            if require_edge:
                print(
                    "edge baseline: "
                    f"flag=0x{baseline_flag:02x} q_like={baseline_q}"
                )
                if baseline_flag != 0 and baseline_q >= max(0, min_queue_byte):
                    print(
                        "edge mode note: baseline is already transfer-ready; "
                        "waiting for a NEW edge (flag rise or q_like increase)"
                    )

        if require_edge and not edge_seen:
            # A "new event" is either a flag rise (0->nonzero)
            # or an increase in the queue-like byte.
            if (baseline_flag == 0 and flag != 0) or (q_like > baseline_q):
                edge_seen = True
                print(
                    "edge detected: "
                    f"flag 0x{baseline_flag:02x}->0x{flag:02x}, "
                    f"q_like {baseline_q}->{q_like}"
                )

        print(
            f"info04 raw={p4.hex()} flag=0x{flag:02x} "
            f"q_like={q_like} consec={consec} edge={'1' if edge_seen else '0'}"
        )

        ready_ok = flag != 0
        queue_ok = (min_queue_byte <= 0) or (q_like >= min_queue_byte)
        if edge_seen and ready_ok and queue_ok:
            consec += 1
            last_flag = flag
            last_q = q_like
            if consec >= max(1, require_hits):
                return last_flag, last_q
        else:
            consec = 0
            last_flag = 0
            last_q = 0

        if asyncio.get_event_loop().time() >= deadline:
            return 0, 0
        await asyncio.sleep(poll_delay)


async def prepare_transfer_mode(cli: LinkClient, trace: Optional[TraceRecorder]) -> bool:
    print("prepare: (85,00)")
    op1, op2, p = await exchange_traced(cli, trace, 0x85, 0x00, timeout=3.0)
    print(f"  <- ({op1:02x},{op2:02x}) {p.hex()}")

    print("prepare: (85,01)")
    op1, op2, p = await exchange_traced(
        cli,
        trace,
        0x85,
        0x01,
        bytes.fromhex("05" + "00" * 8),
        timeout=3.0,
    )
    print(f"  <- ({op1:02x},{op2:02x}) {p.hex()}")

    print("prepare: (85,00) re-check")
    op1, op2, p = await exchange_traced(cli, trace, 0x85, 0x00, timeout=3.0)
    print(f"  <- ({op1:02x},{op2:02x}) {p.hex()}")
    return True


async def main_async(args: argparse.Namespace) -> int:
    address = args.address
    if args.wide_default and (not args.address or args.address == MINI_EVO_ADDR):
        address = DEFAULT_ADDR

    args.out_dir.mkdir(parents=True, exist_ok=True)

    cli = LinkClient(address=address, verbose=args.verbose)
    trace = TraceRecorder()
    jpeg = bytearray()
    empty_runs = 0
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    trace_out = args.trace_out or (args.out_dir / f"share_no_meta_{ts}.trace")

    try:
        await cli.connect()
        await cli.flush()
        try:
            await exchange_traced(cli, trace, 0x00, 0x00, timeout=3.0)
        except Exception as e:
            print(f"hello timeout/non-fatal: {type(e).__name__}: {e}")
            trace.note(f"hello_nonfatal={type(e).__name__}:{e}")

        model = await read_device_info_safe(cli, 1)
        serial = await read_device_info_safe(cli, 2)
        print(f"model={model or '?'} serial={serial or '?'} address={address}")
        trace.note(f"model={model or '?'} serial={serial or '?'} address={address}")

        if model.upper() == "FI019":
            print("note: FI019 usually disconnects on (88,00); this probe validates that directly.")

        if not args.skip_85_prepare:
            try:
                await prepare_transfer_mode(cli, trace)
            except Exception as e:
                print(f"prepare failed: {e}")
                return 2

        if args.arm_delay > 0:
            print(f"arming delay: {args.arm_delay:.1f}s (enter Share mode now)")
            await asyncio.sleep(args.arm_delay)

        print("\nPress Share on camera now (if not already), then waiting for transfer-ready flag...")
        flag, q_like = await wait_share_flag_stable(
            cli,
            timeout=args.wait_share,
            poll_delay=args.poll_delay,
            require_hits=args.require_flag_hits,
            min_queue_byte=args.min_queue_byte,
            require_edge=args.require_edge,
        )
        trace.note(f"share_flag=0x{flag:02x} q_like={q_like}")
        if flag == 0:
            print("transfer-ready flag never appeared; aborting")
            return 3

        print(
            f"flag gate met: ready=0x{flag:02x}, q_like={q_like} "
            f"(min_queue_byte={args.min_queue_byte})"
        )

        if args.post_flag_delay > 0:
            print(f"flag stable; settling {args.post_flag_delay:.2f}s before pull start")
            await asyncio.sleep(args.post_flag_delay)

        print("start transfer without metadata: sending (88,00)")
        op1, op2, ack = await exchange_traced(cli, trace, 0x88, 0x00, timeout=6.0)
        print(f"(88,00) <- ({op1:02x},{op2:02x}) {ack.hex()}")
        if not (op1 == 0x88 and op2 == 0x00 and len(ack) >= 1 and ack[0] == 0x00):
            print("(88,00) not accepted; aborting")
            return 4

        print(f"requesting chunks via (88,02), max={args.max_chunks}, skipping (88,01) metadata")
        for idx in range(args.max_chunks):
            trace.tx(0x88, 0x02, idx.to_bytes(4, "big"))
            await cli.write(0x88, 0x02, idx.to_bytes(4, "big"))
            try:
                op1, op2, p = await cli.recv(timeout=args.chunk_timeout)
            except asyncio.TimeoutError:
                print(f"chunk {idx}: timeout")
                trace.note(f"chunk {idx}: timeout")
                break
            trace.rx(op1, op2, p)

            if op1 != 0x88 or op2 != 0x02:
                print(f"chunk {idx}: unexpected frame ({op1:02x},{op2:02x}) {p.hex()}")
                continue

            if len(p) < 5:
                print(f"chunk {idx}: short payload {p.hex()}")
                empty_runs += 1
            else:
                data = p[5:]
                jpeg.extend(data)
                print(f"chunk {idx}: payload={len(p)} data={len(data)}")
                if len(data) == 0:
                    empty_runs += 1
                else:
                    empty_runs = 0

            if empty_runs >= args.empty_stop:
                print(f"stopping: {empty_runs} consecutive empty chunks")
                break

            # Stop early if we already have a complete JPEG marker pair.
            d = bytes(jpeg)
            soi = d.find(b"\xff\xd8")
            eoi = d.rfind(b"\xff\xd9")
            if soi >= 0 and eoi > soi:
                print(f"jpeg marker complete at chunk {idx}")
                break

        # Try to close cleanly even if transfer failed.
        try:
            op1, op2, p = await exchange_traced(cli, trace, 0x88, 0x03, timeout=3.0)
            print(f"(88,03) <- ({op1:02x},{op2:02x}) {p.hex()}")
        except Exception as e:
            print(f"(88,03) close failed: {e}")
            trace.note(f"(88,03) close failed: {e}")

        try:
            op1, op2, p = await exchange_traced(
                cli,
                trace,
                0x88,
                0x05,
                b"\x00\x00\x00\x00",
                timeout=3.0,
            )
            print(f"(88,05) <- ({op1:02x},{op2:02x}) {p.hex()}")
        except Exception as e:
            print(f"(88,05) complete failed: {e}")
            trace.note(f"(88,05) complete failed: {e}")

        raw = bytes(jpeg)
        soi = raw.find(b"\xff\xd8")
        eoi = raw.rfind(b"\xff\xd9")
        if soi >= 0 and eoi > soi:
            out = raw[soi:eoi + 2]
            out_path = args.out_dir / f"share_no_meta_{model or 'unknown'}_{ts}.jpg"
            out_path.write_bytes(out)
            print(f"SUCCESS: saved {len(out):,} B -> {out_path}")
            trace.note(f"saved_jpeg={out_path} bytes={len(out)}")
            return 0

        print(f"no complete JPEG reconstructed (buffer={len(raw)} B)")
        trace.note(f"no_complete_jpeg buffer={len(raw)}")
        return 5

    except Exception as e:
        print(f"probe failed/disconnected: {type(e).__name__}: {e}")
        trace.note(f"probe_failed={type(e).__name__}:{e}")
        return 6
    finally:
        try:
            trace.save(trace_out)
            print(f"trace saved -> {trace_out}")
        except Exception as e:
            print(f"trace save failed: {e}")
        await cli.disconnect()


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
