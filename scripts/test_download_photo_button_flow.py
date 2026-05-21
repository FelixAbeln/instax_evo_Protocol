from __future__ import annotations

import argparse
import asyncio
import math
from datetime import datetime
from pathlib import Path

from instax_lab.protocol import MINI_EVO_ADDR
from scripts.fi019_common import LinkClient


FLASH_VALUES = {
    "auto": 0,
    "on": 1,
    "off": 2,
}

OFFICIAL_FLASH_ACK = bytes.fromhex("000b00000000")


class TraceRecorder:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def record(self, direction: str, op1: int, op2: int, payload: bytes) -> None:
        self._lines.append(f"{direction} {op1:02x} {op2:02x} {payload.hex()}")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self._lines) + ("\n" if self._lines else ""), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Follow the newly extracted official 82 flow: prepare live view, wait for real frames, "
            "optionally send flash, run 82,10/20/21/22, then resume live-view traffic"
        )
    )
    p.add_argument("--address", default=MINI_EVO_ADDR, help="Camera BLE address")
    p.add_argument("--timeout", type=float, default=3.0, help="Per-response timeout seconds")
    p.add_argument("--poll-count", type=int, default=60, help="Number of 82,20 polls before giving up")
    p.add_argument("--poll-delay", type=float, default=0.5, help="Delay between not-ready 82,20 polls")
    p.add_argument("--chunk-timeout", type=float, default=10.0, help="Per-chunk 82,21 timeout")
    p.add_argument("--open-liveview-first", action="store_true", help="Open live view before running the download flow")
    p.add_argument("--warmup-pulls", type=int, default=6, help="82,01 pulls before stopping live view")
    p.add_argument("--min-frame-bytes", type=int, default=100, help="Treat 82,01 payloads at or above this size as real frames")
    p.add_argument("--min-good-frames", type=int, default=3, help="Number of real live-view frames to observe before flash/transfer")
    p.add_argument("--post-flash-pulls", type=int, default=3, help="Extra 82,01 pulls after flash write before 82,10")
    p.add_argument(
        "--stop-liveview-before-transfer",
        action="store_true",
        help="Legacy fallback: send 82,02 before 82,10 instead of following the official keep-pulling flow",
    )
    p.add_argument("--resume-pulls", type=int, default=3, help="82,01 pulls immediately after 82,22 before reopen")
    p.add_argument("--reopen-liveview-after-transfer", action="store_true", help="Reopen 82,00 and pull frames again after the download flow")
    p.add_argument("--reopen-pulls", type=int, default=4, help="82,01 pulls after the post-transfer reopen")
    p.add_argument("--flash-mode", choices=sorted(FLASH_VALUES), help="Send the app's flash write before the download flow")
    p.add_argument("--out-dir", type=Path, default=Path("captures/image_transfer"), help="Directory for saved JPEG")
    p.add_argument("--trace-out", type=Path, help="Write every TX/RX message as hex lines for byte-by-byte comparison")
    p.add_argument(
        "--direct-chunk-probe",
        action="store_true",
        help="Skip 82,10/82,20 and send 82,21 directly to probe camera response",
    )
    p.add_argument(
        "--direct-chunk-index",
        type=int,
        default=0,
        help="Chunk index for --direct-chunk-probe (default: 0)",
    )
    return p.parse_args()


async def recv_op(cli: LinkClient, op1_want: int, op2_want: int, timeout: float) -> bytes:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            raise asyncio.TimeoutError(f"timeout waiting for ({op1_want:02x},{op2_want:02x})")
        op1, op2, payload = await cli.recv(timeout=left)
        if op1 == op1_want and op2 == op2_want:
            return payload


async def recv_any(cli: LinkClient, timeout: float) -> tuple[int, int, bytes] | None:
    try:
        return await cli.recv(timeout=timeout)
    except asyncio.TimeoutError:
        return None


def is_real_frame(payload: bytes, min_frame_bytes: int) -> bool:
    return len(payload) >= min_frame_bytes or (b"\xff\xd8" in payload and b"\xff\xd9" in payload)


def byte_diff(expected: bytes, actual: bytes) -> str:
    changes: list[str] = []
    for idx, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            changes.append(f"{idx}:{left:02x}->{right:02x}")
    if len(expected) != len(actual):
        changes.append(f"len:{len(expected)}->{len(actual)}")
    return ", ".join(changes) if changes else "(no changes)"


async def observe_responses(cli: LinkClient, label: str, count: int, timeout: float) -> list[tuple[int, int, bytes]]:
    seen: list[tuple[int, int, bytes]] = []
    for idx in range(count):
        r = await recv_any(cli, timeout)
        if r is None:
            print(f"{label} resp {idx + 1}/{count}: timeout")
            continue
        op1, op2, payload = r
        print(f"{label} resp {idx + 1}/{count}: op=({op1:02x},{op2:02x}) len={len(payload)} p={payload.hex()}")
        seen.append(r)
    return seen


async def prepare_liveview(cli: LinkClient, timeout: float) -> bool:
    await cli.flush()
    await cli.write(0x80, 0x15, bytes(17))
    try:
        ack = await recv_op(cli, 0x80, 0x15, timeout)
        print(f"live-view prepare ack: {ack.hex()}")
        return True
    except Exception as e:
        print(f"live-view prepare ack missing: {e}")
        return False


async def open_liveview(cli: LinkClient, timeout: float) -> bool:
    await cli.flush()
    await cli.write(0x82, 0x00, b"\x00")
    try:
        ack = await recv_op(cli, 0x82, 0x00, timeout)
        print(f"live-view open ack: {ack.hex()}")
        return True
    except Exception as e:
        print(f"live-view open failed: {e}")
        return False


async def pull_liveview_frames(cli: LinkClient, pulls: int, timeout: float, min_frame_bytes: int) -> tuple[int, int]:
    real_frames = 0
    short_frames = 0
    for idx in range(pulls):
        await cli.write(0x82, 0x01)
        r = await recv_any(cli, timeout)
        if r is None:
            print(f"warmup pull {idx + 1}/{pulls}: timeout")
            continue
        op1, op2, payload = r
        print(f"warmup pull {idx + 1}/{pulls}: op=({op1:02x},{op2:02x}) len={len(payload)}")
        if op1 == 0x82 and op2 == 0x01:
            if is_real_frame(payload, min_frame_bytes):
                real_frames += 1
            else:
                short_frames += 1
    return real_frames, short_frames


async def wait_for_real_frames(
    cli: LinkClient,
    pulls: int,
    timeout: float,
    min_frame_bytes: int,
    min_good_frames: int,
    label: str,
) -> tuple[int, int]:
    real_frames = 0
    short_frames = 0
    for idx in range(pulls):
        await cli.write(0x82, 0x01)
        r = await recv_any(cli, timeout)
        if r is None:
            print(f"{label} pull {idx + 1}/{pulls}: timeout")
            continue
        op1, op2, payload = r
        print(f"{label} pull {idx + 1}/{pulls}: op=({op1:02x},{op2:02x}) len={len(payload)}")
        if op1 == 0x82 and op2 == 0x01:
            if is_real_frame(payload, min_frame_bytes):
                real_frames += 1
            else:
                short_frames += 1
                print(f"{label} pull {idx + 1}/{pulls}: short 82,01 payload={payload.hex()}")
        else:
            print(f"{label} pull {idx + 1}/{pulls}: non-frame payload={payload.hex()}")
        if real_frames >= min_good_frames:
            break
    return real_frames, short_frames


async def stop_liveview(cli: LinkClient, timeout: float) -> None:
    await cli.flush()
    await cli.write(0x82, 0x02, b"\x00")
    try:
        ack = await recv_op(cli, 0x82, 0x02, timeout)
        print(f"live-view close ack: {ack.hex()}")
    except Exception as e:
        print(f"live-view close ack missing: {e}")


async def set_flash_mode(cli: LinkClient, mode_name: str, timeout: float) -> bool:
    value = FLASH_VALUES[mode_name]
    payload = bytes([0x0B, 0x02, value, 0x00, 0x00, 0x00])
    await cli.flush()
    print(f"flash tx: op=(80,11) payload={payload.hex()}")
    await cli.write(0x80, 0x11, payload)
    responses = await observe_responses(cli, label="flash", count=4, timeout=min(timeout, 0.5))
    for op1, op2, ack in responses:
        if op1 == 0x80 and op2 == 0x11:
            print(f"flash {mode_name} ack candidate: {ack.hex()}")
            print(f"flash compare vs official: {byte_diff(OFFICIAL_FLASH_ACK, ack)}")
            return ack == OFFICIAL_FLASH_ACK
    print(f"flash {mode_name} no 80,11 response observed (payload={payload.hex()})")
    return False


async def run_download_photo_flow(
    cli: LinkClient,
    timeout: float,
    poll_count: int,
    poll_delay: float,
    chunk_timeout: float,
) -> bytes | None:
    await cli.flush()

    print("download-photo flow: send (82,10) 00")
    await cli.write(0x82, 0x10, b"\x00")
    try:
        p = await recv_op(cli, 0x82, 0x10, timeout)
    except asyncio.TimeoutError:
        print("download-photo flow: no response to (82,10)")
        return None
    print(f"download-photo flow: (82,10) ack={p.hex()}")

    total_size = 0
    chunk_size = 0
    ready = False
    for attempt in range(poll_count):
        await cli.write(0x82, 0x20)
        try:
            p = await recv_op(cli, 0x82, 0x20, timeout)
        except asyncio.TimeoutError:
            print("download-photo flow: poll timeout")
            return None
        if len(p) >= 10:
            total_size = int.from_bytes(p[2:6], "big")
            chunk_size = int.from_bytes(p[6:10], "big")
            ready = True
            print(
                "download-photo flow: READY "
                f"total={total_size} chunk={chunk_size} chunks={math.ceil(total_size / chunk_size) if chunk_size else 0}"
            )
            break
        print(f"download-photo flow: not ready poll {attempt + 1}/{poll_count} payload={p.hex()}")
        await asyncio.sleep(poll_delay)

    if not ready:
        print("download-photo flow: image not ready after poll window")
        return None

    jpeg = bytearray()
    num_chunks = math.ceil(total_size / chunk_size) if chunk_size else 0
    for chunk_idx in range(num_chunks):
        await cli.write(0x82, 0x21, chunk_idx.to_bytes(4, "big"))
        got_chunk = False
        for _ in range(10):
            try:
                op1, op2, cp = await cli.recv(timeout=chunk_timeout)
            except asyncio.TimeoutError:
                print("download-photo flow: chunk timeout")
                break
            if op1 == 0x82 and op2 == 0x21:
                if len(cp) >= 5:
                    jpeg.extend(cp[5:])
                got_chunk = True
                break
            print(f"download-photo flow: skipping unsolicited frame ({op1:02x},{op2:02x})")
        if not got_chunk:
            break
        if chunk_idx % 5 == 0 or chunk_idx == num_chunks - 1:
            print(f"download-photo flow: chunk {chunk_idx + 1}/{num_chunks}")

    await cli.write(0x82, 0x22)
    try:
        p = await recv_op(cli, 0x82, 0x22, 2.0)
        print(f"download-photo flow: (82,22) ack={p.hex()}")
    except asyncio.TimeoutError:
        print("download-photo flow: no (82,22) ack")

    if len(jpeg) <= 100:
        print("download-photo flow: too few bytes received")
        return None

    data = bytes(jpeg)
    soi = data.find(b"\xff\xd8")
    if soi < 0:
        print("download-photo flow: no JPEG SOI")
        return None
    return data[soi:]


async def run_direct_chunk_probe(
    cli: LinkClient,
    timeout: float,
    chunk_index: int,
) -> tuple[bool, bytes | None]:
    await cli.flush()
    payload = chunk_index.to_bytes(4, "big", signed=False)
    print(f"direct-chunk probe: send (82,21) idx={chunk_index} payload={payload.hex()}")
    await cli.write(0x82, 0x21, payload)

    r = await recv_any(cli, timeout)
    if r is None:
        print("direct-chunk probe: timeout waiting for response")
        return False, None

    op1, op2, p = r
    print(f"direct-chunk probe: rx op=({op1:02x},{op2:02x}) len={len(p)} payload={p.hex()}")
    if op1 == 0x82 and op2 == 0x21:
        return True, p
    return False, p


async def resume_liveview_after_transfer(
    cli: LinkClient,
    timeout: float,
    resume_pulls: int,
    reopen_liveview_after_transfer: bool,
    reopen_pulls: int,
    min_frame_bytes: int,
) -> None:
    print("post-transfer: continue 82,01 pulls")
    await wait_for_real_frames(
        cli,
        pulls=resume_pulls,
        timeout=timeout,
        min_frame_bytes=min_frame_bytes,
        min_good_frames=1,
        label="resume",
    )

    if not reopen_liveview_after_transfer:
        return

    print("post-transfer: reopen live view with (82,00)")
    reopened = await open_liveview(cli, timeout)
    if not reopened:
        return
    await wait_for_real_frames(
        cli,
        pulls=reopen_pulls,
        timeout=timeout,
        min_frame_bytes=min_frame_bytes,
        min_good_frames=1,
        label="reopen",
    )


async def main_async(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cli = LinkClient(address=args.address, verbose=True)
    trace = TraceRecorder() if args.trace_out else None
    try:
        await cli.connect()
        await cli.hello()
        model = await cli.read_device_info(1)
        print(f"model={model} address={args.address}")

        if trace is not None:
            orig_write = cli.write
            orig_recv = cli.recv

            async def traced_write(op1: int, op2: int, payload: bytes = b"") -> None:
                trace.record("TX", op1, op2, payload)
                await orig_write(op1, op2, payload)

            async def traced_recv(timeout: float = 5.0) -> tuple[int, int, bytes]:
                op1, op2, payload = await orig_recv(timeout=timeout)
                trace.record("RX", op1, op2, payload)
                return op1, op2, payload

            cli.write = traced_write  # type: ignore[method-assign]
            cli.recv = traced_recv  # type: ignore[method-assign]

        if args.open_liveview_first:
            prepared = await prepare_liveview(cli, args.timeout)
            print(f"live-view prepare result: {'ack' if prepared else 'no-ack'}")
            lv_ok = await open_liveview(cli, args.timeout)
            if not lv_ok and not prepared:
                print("live-view open retry: prepare had no ack; retrying bare (82,00)")
                await cli.flush()
                lv_ok = await open_liveview(cli, args.timeout)
            if not lv_ok:
                return 2
            real_frames, short_frames = await wait_for_real_frames(
                cli,
                pulls=args.warmup_pulls,
                timeout=args.timeout,
                min_frame_bytes=args.min_frame_bytes,
                min_good_frames=args.min_good_frames,
                label="warmup",
            )
            print(f"warmup summary: real_frames={real_frames} short_frames={short_frames}")

        if args.flash_mode:
            await set_flash_mode(cli, args.flash_mode, args.timeout)
            if args.open_liveview_first and args.post_flash_pulls > 0:
                await wait_for_real_frames(
                    cli,
                    pulls=args.post_flash_pulls,
                    timeout=args.timeout,
                    min_frame_bytes=args.min_frame_bytes,
                    min_good_frames=1,
                    label="post-flash",
                )

        if args.open_liveview_first and args.stop_liveview_before_transfer:
            print("download-photo flow: legacy stop live view before transfer")
            await stop_liveview(cli, args.timeout)
        elif args.open_liveview_first:
            print("download-photo flow: keep live-view polling state into 82 transfer (official-style)")

        if args.direct_chunk_probe:
            ok, payload = await run_direct_chunk_probe(
                cli,
                timeout=args.timeout,
                chunk_index=args.direct_chunk_index,
            )
            if not ok:
                return 4
            if payload is not None and len(payload) > 8 and b"\xff\xd8" in payload:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                out_path = args.out_dir / f"direct_chunk_{ts}_{model or 'unknown'}.bin"
                out_path.write_bytes(payload)
                print(f"saved direct chunk payload {out_path} ({len(payload)} bytes)")
        else:
            jpeg = await run_download_photo_flow(
                cli,
                timeout=args.timeout,
                poll_count=args.poll_count,
                poll_delay=args.poll_delay,
                chunk_timeout=args.chunk_timeout,
            )
            if not jpeg:
                return 3

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            out_path = args.out_dir / f"download_photo_{ts}_{model or 'unknown'}.jpg"
            out_path.write_bytes(jpeg)
            print(f"saved {out_path} ({len(jpeg)} bytes)")

        if args.open_liveview_first:
            await resume_liveview_after_transfer(
                cli,
                timeout=args.timeout,
                resume_pulls=args.resume_pulls,
                reopen_liveview_after_transfer=args.reopen_liveview_after_transfer,
                reopen_pulls=args.reopen_pulls,
                min_frame_bytes=args.min_frame_bytes,
            )
        return 0
    finally:
        if trace is not None and args.trace_out is not None:
            trace.save(args.trace_out)
            print(f"trace saved to {args.trace_out}")
        await cli.disconnect()


def main() -> int:
    return asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())