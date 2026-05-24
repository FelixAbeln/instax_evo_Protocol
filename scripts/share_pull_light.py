#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional

from bleak import BleakClient, BleakScanner

# Standalone constants (no instax_lab imports)
DEFAULT_ADDR = "FA:AB:BC:11:6F:D2"  # Mini Evo (FI019)
WRITE_UUID = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"
BLE_WRITE_CHUNK = 182


def make_packet(op1: int, op2: int, payload: bytes = b"") -> bytes:
    header = b"\x41\x62"
    length = struct.pack(">H", 7 + len(payload))
    body = header + length + bytes([op1, op2]) + payload
    cs = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])


class Trace:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def tx(self, op1: int, op2: int, payload: bytes = b"") -> None:
        self.lines.append(f"TX {op1:02x} {op2:02x} {payload.hex()}")

    def rx(self, op1: int, op2: int, payload: bytes = b"") -> None:
        self.lines.append(f"RX {op1:02x} {op2:02x} {payload.hex()}")

    def note(self, text: str) -> None:
        self.lines.append(f"# {text}")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


class LightLink:
    def __init__(self, address: str, verbose: bool = False):
        self.address = address
        self.verbose = verbose
        self._client: Optional[BleakClient] = None
        self._rxq: asyncio.Queue[tuple[int, int, bytes]] = asyncio.Queue()
        self._buf = bytearray()

    def _log(self, text: str) -> None:
        if self.verbose:
            print(text)

    def _on_notify(self, _sender: int, data: bytearray) -> None:
        self._buf.extend(data)
        while len(self._buf) >= 4:
            if self._buf[0] != 0x61 or self._buf[1] != 0x42:
                self._buf.clear()
                return
            total = struct.unpack_from(">H", self._buf, 2)[0]
            if len(self._buf) < total:
                return
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            op1, op2 = frame[4], frame[5]
            payload = frame[6:total - 1] if total > 7 else b""
            self._rxq.put_nowait((op1, op2, payload))

    async def connect(self, timeout: float = 20.0) -> None:
        dev = await BleakScanner.find_device_by_filter(
            lambda d, _a: d.address.upper() == self.address.upper(),
            timeout=timeout,
        )
        if not dev:
            raise RuntimeError(f"Device not found: {self.address}")

        last_err: Exception | None = None
        for attempt in range(1, 6):
            try:
                self._client = BleakClient(self.address, timeout=30)
                await self._client.connect()

                get_services = getattr(self._client, "get_services", None)
                if callable(get_services):
                    await get_services()
                await asyncio.sleep(1.0)

                for n_try in range(1, 4):
                    try:
                        await self._client.start_notify(NOTIFY_UUID, self._on_notify)
                        self._log(f"connected {self.address} mtu={self._client.mtu_size}")
                        return
                    except Exception as e:
                        last_err = e
                        if n_try == 3:
                            raise
                        await asyncio.sleep(1.0)
            except Exception as e:
                last_err = e
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                await asyncio.sleep(2.0)

        raise RuntimeError(f"Unable to establish stable notify session: {last_err}")

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._buf.clear()

    async def flush(self) -> None:
        while True:
            try:
                self._rxq.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def write(self, op1: int, op2: int, payload: bytes = b"") -> None:
        if not self._client:
            raise RuntimeError("not connected")
        pkt = make_packet(op1, op2, payload)
        for off in range(0, len(pkt), BLE_WRITE_CHUNK):
            await self._client.write_gatt_char(
                WRITE_UUID,
                bytearray(pkt[off:off + BLE_WRITE_CHUNK]),
                response=False,
            )

    async def recv(self, timeout: float = 5.0) -> tuple[int, int, bytes]:
        return await asyncio.wait_for(self._rxq.get(), timeout=timeout)


async def send_only(cli: LightLink, tr: Trace, op1: int, op2: int, payload: bytes = b"") -> None:
    tr.tx(op1, op2, payload)
    print(f"TX ({op1:02x},{op2:02x}) len={len(payload)} payload={payload.hex()}")
    await cli.write(op1, op2, payload)


async def recv_match(
    cli: LightLink,
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
        op1, op2, p = await cli.recv(timeout=left)
        tr.rx(op1, op2, p)
        note = ""
        if (op1, op2) != (want_op1, want_op2):
            note = f" [unexpected, waiting for ({want_op1:02x},{want_op2:02x})]"
        elif payload_predicate is not None and not payload_predicate(p):
            note = " [unexpected payload]"
        else:
            note = " [match]"
        print(f"RX ({op1:02x},{op2:02x}) len={len(p)} payload={p.hex()}{note}")
        if (op1, op2) != (want_op1, want_op2):
            continue
        if payload_predicate is not None and not payload_predicate(p):
            continue
        return op1, op2, p


async def exchange(
    cli: LightLink,
    tr: Trace,
    op1: int,
    op2: int,
    payload: bytes = b"",
    timeout: float = 5.0,
) -> tuple[int, int, bytes]:
    await send_only(cli, tr, op1, op2, payload)
    return await recv_match(cli, tr, op1, op2, timeout=timeout)


async def read_device_info(cli: LightLink, tr: Trace, info_type: int) -> str:
    await send_only(cli, tr, 0x00, 0x01, bytes([info_type]))
    _o1, _o2, p = await recv_match(
        cli,
        tr,
        0x00,
        0x01,
        timeout=3.0,
        payload_predicate=lambda x: len(x) >= 2 and x[1] == info_type,
    )
    if len(p) < 4:
        return ""
    n = p[2]
    return p[3:3 + n].decode("ascii", errors="replace")


async def wait_ready_flag(
    cli: LightLink,
    tr: Trace,
    timeout: float,
    poll_delay: float,
    require_edge: bool = False,
) -> tuple[int, bytes]:
    deadline = asyncio.get_event_loop().time() + timeout
    last = b""
    baseline_ready: Optional[int] = None
    baseline_q: Optional[int] = None
    edge_seen = not require_edge
    while True:
        await send_only(cli, tr, 0x00, 0x02, b"\x04")
        _o1, _o2, p = await recv_match(
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

        if baseline_ready is None:
            baseline_ready = ready
            baseline_q = q_like
            print(
                f"info04 baseline ready=0x{baseline_ready:02x} q_like={baseline_q}"
            )
            tr.note(
                f"info04_baseline_ready=0x{baseline_ready:02x} q_like={baseline_q}"
            )

        if require_edge and not edge_seen and baseline_ready is not None and baseline_q is not None:
            # Accept either a fresh ready rise or a queue-like increment.
            if (baseline_ready == 0 and ready != 0) or (q_like > baseline_q):
                edge_seen = True
                print(
                    f"info04 edge detected: ready 0x{baseline_ready:02x}->0x{ready:02x} "
                    f"q_like {baseline_q}->{q_like}"
                )
                tr.note(
                    f"info04_edge ready 0x{baseline_ready:02x}->0x{ready:02x} q_like {baseline_q}->{q_like}"
                )

        print(f"info04 decode ready=0x{ready:02x} q_like={q_like}")
        if ready != 0 and edge_seen:
            return ready, p
        if asyncio.get_event_loop().time() >= deadline:
            return 0, last
        await asyncio.sleep(poll_delay)


def _parse_watch_subs(spec: str) -> list[int]:
    out: list[int] = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        base = 16 if tok.lower().startswith("0x") else 10
        val = int(tok, base) & 0xFF
        out.append(val)
    if not out:
        out = [0x02, 0x03, 0x01, 0x04, 0x05]
    return out


def _parse_op2_list(spec: str) -> list[int]:
    out: list[int] = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        base = 16 if tok.lower().startswith("0x") else 10
        out.append(int(tok, base) & 0xFF)
    return out


def _parse_sub_list(spec: str) -> list[int]:
    out: list[int] = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        base = 16 if tok.lower().startswith("0x") else 10
        out.append(int(tok, base) & 0xFF)
    return out


def _parse_range_list(spec: str) -> list[int]:
    out: list[int] = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            base_a = 16 if a.strip().lower().startswith("0x") else 10
            base_b = 16 if b.strip().lower().startswith("0x") else 10
            va = int(a.strip(), base_a) & 0xFF
            vb = int(b.strip(), base_b) & 0xFF
            if va <= vb:
                out.extend(range(va, vb + 1))
            else:
                out.extend(range(va, vb - 1, -1))
        else:
            base = 16 if tok.lower().startswith("0x") else 10
            out.append(int(tok, base) & 0xFF)
    return out


def _parse_probe_seq(spec: str) -> list[tuple[int, int, bytes | None]]:
    out: list[tuple[int, int, bytes | None]] = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        payload_override: bytes | None = None
        if "@" in tok:
            tok, payload_hex = tok.split("@", 1)
            payload_hex = payload_hex.strip()
            if payload_hex:
                payload_override = bytes.fromhex(payload_hex)
        sep = ":" if ":" in tok else ("/" if "/" in tok else None)
        if sep is None:
            raise ValueError(
                f"invalid probe-seq token '{tok}', expected op1:op2 or op1/op2"
            )
        a, b = tok.split(sep, 1)
        base_a = 16 if a.strip().lower().startswith("0x") else 10
        base_b = 16 if b.strip().lower().startswith("0x") else 10
        op1 = int(a.strip(), base_a) & 0xFF
        op2 = int(b.strip(), base_b) & 0xFF
        out.append((op1, op2, payload_override))
    return out


def _probe_payload_for_88(op2: int, args: argparse.Namespace) -> bytes:
    if op2 == 0x01:
        return bytes.fromhex(args.meta_payload_hex)
    if op2 == 0x02:
        return int(args.start_chunk).to_bytes(4, "big")
    if op2 == 0x05:
        return b"\x00\x00\x00\x00"
    return b""


def _probe_payload_for_op(op1: int, op2: int, args: argparse.Namespace) -> bytes:
    if op1 == 0x88:
        return _probe_payload_for_88(op2, args)
    if args.probe_payload_hex:
        return bytes.fromhex(args.probe_payload_hex)
    return b""


async def read_support_sub(cli: LightLink, tr: Trace, sub: int, timeout: float = 3.0) -> bytes:
    await send_only(cli, tr, 0x00, 0x02, bytes([sub]))
    _o1, _o2, p = await recv_match(
        cli,
        tr,
        0x00,
        0x02,
        timeout=timeout,
        payload_predicate=lambda x: len(x) >= 2 and x[1] == sub,
    )
    return p


def _brief_support_decode(sub: int, p: bytes) -> str:
    if sub == 0x04:
        ready = p[4] if len(p) > 4 else 0
        q_like = p[5] if len(p) > 5 else 0
        return f"ready=0x{ready:02x} q_like={q_like}"
    if sub == 0x01:
        state = p[2] if len(p) > 2 else None
        pct = p[3] if len(p) > 3 else None
        if state is not None and pct is not None:
            return f"battery_state={state} battery_pct={pct}"
    if sub == 0x02:
        if len(p) > 2:
            return f"photos_left={p[2] & 0x0F} status=0x{p[2]:02x}"
    if sub == 0x05:
        if len(p) > 5:
            return f"shot_counter={p[5]}"
    if sub == 0x03:
        if len(p) >= 10:
            tcnt = struct.unpack_from(">I", p, 2)[0]
            pcnt = struct.unpack_from(">I", p, 6)[0]
            return f"transfer_count={tcnt} print_count={pcnt}"
    return ""


async def watch_support_changes(
    cli: LightLink,
    tr: Trace,
    watch_subs: list[int],
    watch_seconds: float,
    poll_delay: float,
) -> None:
    deadline = asyncio.get_event_loop().time() + max(1.0, watch_seconds)
    last_by_sub: dict[int, bytes] = {}
    while True:
        if asyncio.get_event_loop().time() >= deadline:
            print("watch window complete")
            return
        for sub in watch_subs:
            p = await read_support_sub(cli, tr, sub, timeout=3.0)
            prev = last_by_sub.get(sub)
            changed = prev is not None and prev != p
            first = prev is None
            last_by_sub[sub] = p
            dec = _brief_support_decode(sub, p)
            label = "first" if first else ("changed" if changed else "same")
            dec_txt = f" {dec}" if dec else ""
            print(f"support sub=0x{sub:02x} {label} raw={p.hex()}{dec_txt}")
        await asyncio.sleep(max(0.0, poll_delay))


async def close_transfer_session(cli: LightLink, tr: Trace, timeout: float = 3.0) -> None:
    print("teardown: attempting transfer close (88,03 then 88,05)")
    try:
        await exchange(cli, tr, 0x88, 0x03, timeout=timeout)
    except Exception as e:
        tr.note(f"teardown_close_8803_failed={type(e).__name__}:{e}")
        print(f"teardown close (88,03) failed: {type(e).__name__}: {e}")
    try:
        await exchange(cli, tr, 0x88, 0x05, b"\x00\x00\x00\x00", timeout=timeout)
    except Exception as e:
        tr.note(f"teardown_close_8805_failed={type(e).__name__}:{e}")
        print(f"teardown close (88,05) failed: {type(e).__name__}: {e}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Standalone light share-pull mutation script (no instax_lab imports). "
            "Flow: optional 85 prep -> wait info04 ready -> 88,00 -> optional 88,01 -> chunks -> optional close."
        )
    )
    p.add_argument("--address", default=DEFAULT_ADDR, help="Camera BLE address")
    p.add_argument("--verbose", action="store_true")
    p.add_argument(
        "--scan-timeout",
        type=float,
        default=45.0,
        help="Seconds to wait for BLE advertising match before failing connect",
    )

    p.add_argument("--prep-85", action="store_true", help="Run 85,00 -> 85,01 -> 85,00")
    p.add_argument("--skip-wait-flag", action="store_true", help="Skip info04 ready gate")
    p.add_argument("--wait-share", type=float, default=45.0)
    p.add_argument("--poll-delay", type=float, default=0.5)
    p.add_argument("--post-flag-delay", type=float, default=0.8)
    p.add_argument(
        "--require-share-edge",
        action="store_true",
        help=(
            "Require a new post-connect info04 transition before proceeding "
            "(ready rise or q_like increment)"
        ),
    )
    p.add_argument(
        "--watch-only",
        action="store_true",
        help="Connect and watch support-info changes only; do not send any 88 pull commands",
    )
    p.add_argument(
        "--watch-seconds",
        type=float,
        default=120.0,
        help="Duration for watch-only mode",
    )
    p.add_argument(
        "--watch-subs",
        default="2,3,1,4,5",
        help="Comma-separated support-info subtypes to poll in watch-only mode",
    )

    p.add_argument("--skip-88-01", action="store_true", help="Skip metadata")
    p.add_argument("--meta-payload-hex", default="00000000", help="Payload for 88,01")

    p.add_argument("--chunks", type=int, default=0)
    p.add_argument("--start-chunk", type=int, default=0)
    p.add_argument("--chunk-timeout", type=float, default=8.0)

    p.add_argument("--skip-close", action="store_true")
    p.add_argument(
        "--probe-op1",
        default="0x88",
        help="Probe op1 byte (hex or decimal), for example 0x83 or 0x88",
    )
    p.add_argument(
        "--probe-88-op2s",
        default="",
        help=(
            "Comma-separated op2 values to probe after flag gating, "
            "for example: 0,1,2,3,5"
        ),
    )
    p.add_argument(
        "--probe-payload-hex",
        default="",
        help=(
            "Fixed payload (hex) for non-0x88 probes; when probe-op1=0x88, "
            "built-in op2-specific payload rules are used"
        ),
    )
    p.add_argument(
        "--probe-timeout",
        type=float,
        default=4.0,
        help="Per-probe response timeout",
    )
    p.add_argument(
        "--probe-inter-send-delay",
        type=float,
        default=0.0,
        help="Delay in seconds between probe sends",
    )
    p.add_argument(
        "--probe-only",
        action="store_true",
        help="Run opcode probes only (skip normal pull sequence)",
    )
    p.add_argument(
        "--probe-continue-on-error",
        action="store_true",
        help="Continue probing later opcodes even if one probe times out/fails",
    )
    p.add_argument(
        "--probe-liveness-sub",
        type=int,
        default=4,
        help=(
            "After a failed probe, read support-info sub (0-255) to verify camera/session is still alive; "
            "set to -1 to disable"
        ),
    )
    p.add_argument(
        "--probe-watch-support-subs",
        default="",
        help=(
            "Comma-separated support-info subs to read after each successful probe request "
            "(for state-delta discovery), for example: 1,2,3,4,5"
        ),
    )
    p.add_argument(
        "--probe-op1-list",
        default="",
        help=(
            "Optional op1 scan list/ranges for safe family scan, for example: 0x80-0x87 or 0x83,0x84"
        ),
    )
    p.add_argument(
        "--probe-seq",
        default="",
        help=(
            "Explicit ordered sequence of op1/op2 pairs, for example: 0x80:0,0x80:1,0x83:1,0x83:2"
        ),
    )
    p.add_argument(
        "--probe-stop-family-on-error",
        action="store_true",
        help="In op1-list mode, stop current op1 family immediately on first probe error",
    )
    p.add_argument(
        "--probe-83-select-then-02-max-idx",
        type=int,
        default=-1,
        help=(
            "Run staged 0x83 loop in one session: for idx in range, send 83,01(idx) then 83,02(idx); "
            "set >=0 to enable"
        ),
    )
    p.add_argument(
        "--probe-83-start-idx",
        type=int,
        default=0,
        help="Start index for --probe-83-select-then-02-max-idx staged loop",
    )
    p.add_argument(
        "--probe-83-repeat-02-per-idx",
        type=int,
        default=1,
        help="In staged mode, number of repeated 83,02 requests per index",
    )
    p.add_argument("--out-dir", type=Path, default=Path("captures/image_transfer"))
    p.add_argument("--tag", default="")
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / f"share_light{tag}_{ts}.trace"

    cli = LightLink(address=args.address, verbose=args.verbose)
    tr = Trace()
    jpeg_buf = bytearray()
    attempted_transfer_ops = False

    try:
        await cli.connect(timeout=args.scan_timeout)
        await cli.flush()

        try:
            await exchange(cli, tr, 0x00, 0x00, timeout=3.0)
        except Exception as e:
            tr.note(f"hello_nonfatal={type(e).__name__}:{e}")

        model = ""
        serial = ""
        try:
            model = await read_device_info(cli, tr, 1)
            serial = await read_device_info(cli, tr, 2)
        except Exception as e:
            tr.note(f"device_info_error={type(e).__name__}:{e}")

        print(f"connected model={model or '?'} serial={serial or '?'} addr={args.address}")
        tr.note(f"model={model or '?'} serial={serial or '?'} addr={args.address}")

        if args.prep_85:
            print("prep flow: 85,00 -> 85,01 -> 85,00")
            await exchange(cli, tr, 0x85, 0x00, timeout=3.0)
            await exchange(cli, tr, 0x85, 0x01, bytes.fromhex("05" + "00" * 8), timeout=3.0)
            await exchange(cli, tr, 0x85, 0x00, timeout=3.0)

        if args.watch_only:
            subs = _parse_watch_subs(args.watch_subs)
            tr.note(
                "watch_only subs=" + ",".join(f"0x{x:02x}" for x in subs)
                + f" seconds={args.watch_seconds}"
            )
            print(
                "watch-only mode active; trigger Share now and observe changed rows"
            )
            await watch_support_changes(
                cli=cli,
                tr=tr,
                watch_subs=subs,
                watch_seconds=args.watch_seconds,
                poll_delay=args.poll_delay,
            )
            tr.note("watch_only_complete")
            return 0

        if not args.skip_wait_flag:
            print("waiting for info04 ready flag...")
            ready, raw = await wait_ready_flag(
                cli,
                tr,
                timeout=args.wait_share,
                poll_delay=args.poll_delay,
                require_edge=args.require_share_edge,
            )
            tr.note(f"ready_flag=0x{ready:02x} info04={raw.hex()}")
            if ready == 0:
                print("ready flag did not appear")
                return 2
            if args.post_flag_delay > 0:
                print(f"sleeping after ready flag: {args.post_flag_delay:.2f}s")
                await asyncio.sleep(args.post_flag_delay)

        probe_op1 = int(str(args.probe_op1), 0) & 0xFF
        probe_op1_list = _parse_range_list(args.probe_op1_list)
        probe_seq = _parse_probe_seq(args.probe_seq)
        probe_ops = _parse_op2_list(args.probe_88_op2s)
        probe_watch_subs = _parse_sub_list(args.probe_watch_support_subs)
        support_prev: dict[int, bytes] = {}
        if probe_seq and 0x88 in [x[0] for x in probe_seq]:
            attempted_transfer_ops = True
        if probe_seq:
            print(
                "probe sequence mode: "
                + ",".join(
                    (
                        f"({op1:02x},{op2:02x})"
                        if p is None
                        else f"({op1:02x},{op2:02x}@{p.hex()})"
                    )
                    for op1, op2, p in probe_seq
                )
            )
            if probe_watch_subs:
                print(
                    "probe support-watch subs="
                    + ",".join(f"0x{x:02x}" for x in probe_watch_subs)
                )
            tr.note(
                "probe_seq="
                + ",".join(
                    (
                        f"{op1:02x}:{op2:02x}"
                        if p is None
                        else f"{op1:02x}:{op2:02x}@{p.hex()}"
                    )
                    for op1, op2, p in probe_seq
                )
            )
            for i, (sq_op1, sq_op2, sq_payload_override) in enumerate(probe_seq):
                payload = (
                    sq_payload_override
                    if sq_payload_override is not None
                    else _probe_payload_for_op(sq_op1, sq_op2, args)
                )
                print(f"probe-seq: sending ({sq_op1:02x},{sq_op2:02x}) payload={payload.hex()}")
                try:
                    await exchange(
                        cli,
                        tr,
                        sq_op1,
                        sq_op2,
                        payload,
                        timeout=args.probe_timeout,
                    )
                    if probe_watch_subs:
                        for sub in probe_watch_subs:
                            try:
                                sp = await read_support_sub(
                                    cli,
                                    tr,
                                    sub,
                                    timeout=args.probe_timeout,
                                )
                                prev = support_prev.get(sub)
                                support_prev[sub] = sp
                                changed = prev is not None and prev != sp
                                label = "first" if prev is None else ("changed" if changed else "same")
                                print(
                                    "probe support-watch: "
                                    f"after=({sq_op1:02x},{sq_op2:02x}) sub=0x{sub:02x} "
                                    f"{label} raw={sp.hex()}"
                                )
                            except Exception as se:
                                tr.note(
                                    "probe_support_read_failed "
                                    f"after=({sq_op1:02x},{sq_op2:02x}) sub=0x{sub:02x} "
                                    f"error={type(se).__name__}:{se}"
                                )
                                print(
                                    "probe support-watch: FAILED "
                                    f"after=({sq_op1:02x},{sq_op2:02x}) sub=0x{sub:02x} "
                                    f"error={type(se).__name__}: {se}"
                                )
                except Exception as e:
                    tr.note(f"probe_seq_{sq_op1:02x}_{sq_op2:02x}_error={type(e).__name__}:{e}")
                    print(
                        f"probe-seq ({sq_op1:02x},{sq_op2:02x}) error: {type(e).__name__}: {e}"
                    )
                    if 0 <= args.probe_liveness_sub <= 0xFF:
                        try:
                            lp = await read_support_sub(
                                cli,
                                tr,
                                int(args.probe_liveness_sub) & 0xFF,
                                timeout=args.probe_timeout,
                            )
                            tr.note(
                                f"probe_liveness_ok sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} payload={lp.hex()}"
                            )
                            print(
                                "post-probe liveness: OK "
                                f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} payload={lp.hex()}"
                            )
                        except Exception as le:
                            tr.note(
                                f"probe_liveness_failed sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} "
                                f"error={type(le).__name__}:{le}"
                            )
                            print(
                                "post-probe liveness: FAILED "
                                f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} "
                                f"error={type(le).__name__}: {le}"
                            )
                    if not args.probe_continue_on_error:
                        break
                if args.probe_inter_send_delay > 0 and i < (len(probe_seq) - 1):
                    await asyncio.sleep(args.probe_inter_send_delay)

            if args.probe_only:
                print("probe-only complete")
                tr.note("probe_only_complete")
                return 0

        if probe_ops:
            if (not probe_op1_list and probe_op1 == 0x88) or (probe_op1_list and 0x88 in probe_op1_list):
                attempted_transfer_ops = True
            op1_targets = probe_op1_list if probe_op1_list else [probe_op1]
            print(
                "probe mode: op1 set = "
                + ",".join(f"0x{x:02x}" for x in op1_targets)
                + " op2 set = "
                + ",".join(f"0x{x:02x}" for x in probe_ops)
            )
            if probe_watch_subs:
                print(
                    "probe support-watch subs="
                    + ",".join(f"0x{x:02x}" for x in probe_watch_subs)
                )
            tr.note(
                "probe_op1s=" + ",".join(f"0x{x:02x}" for x in op1_targets)
                + " op2s=" + ",".join(f"0x{x:02x}" for x in probe_ops)
            )
            for op1_idx, active_op1 in enumerate(op1_targets):
                tr.note(f"probe_family_start op1=0x{active_op1:02x}")
                family_had_error = False
                for i, op2 in enumerate(probe_ops):
                    payload = _probe_payload_for_op(active_op1, op2, args)
                    print(
                        f"probe: sending ({active_op1:02x},{op2:02x}) payload={payload.hex()}"
                    )
                    try:
                        await exchange(
                            cli,
                            tr,
                            active_op1,
                            op2,
                            payload,
                            timeout=args.probe_timeout,
                        )
                        if probe_watch_subs:
                            for sub in probe_watch_subs:
                                try:
                                    sp = await read_support_sub(
                                        cli,
                                        tr,
                                        sub,
                                        timeout=args.probe_timeout,
                                    )
                                    prev = support_prev.get(sub)
                                    support_prev[sub] = sp
                                    changed = prev is not None and prev != sp
                                    label = "first" if prev is None else ("changed" if changed else "same")
                                    print(
                                        "probe support-watch: "
                                        f"after=({active_op1:02x},{op2:02x}) sub=0x{sub:02x} "
                                        f"{label} raw={sp.hex()}"
                                    )
                                    if changed:
                                        tr.note(
                                            "probe_support_changed "
                                            f"after=({active_op1:02x},{op2:02x}) sub=0x{sub:02x} "
                                            f"prev={prev.hex()} now={sp.hex()}"
                                        )
                                except Exception as se:
                                    tr.note(
                                        "probe_support_read_failed "
                                        f"after=({active_op1:02x},{op2:02x}) sub=0x{sub:02x} "
                                        f"error={type(se).__name__}:{se}"
                                    )
                                    print(
                                        "probe support-watch: FAILED "
                                        f"after=({active_op1:02x},{op2:02x}) sub=0x{sub:02x} "
                                        f"error={type(se).__name__}: {se}"
                                    )
                    except Exception as e:
                        family_had_error = True
                        tr.note(
                            f"probe_{active_op1:02x}_{op2:02x}_error={type(e).__name__}:{e}"
                        )
                        print(
                            f"probe ({active_op1:02x},{op2:02x}) error: {type(e).__name__}: {e}"
                        )
                        if 0 <= args.probe_liveness_sub <= 0xFF:
                            try:
                                lp = await read_support_sub(
                                    cli,
                                    tr,
                                    int(args.probe_liveness_sub) & 0xFF,
                                    timeout=args.probe_timeout,
                                )
                                tr.note(
                                    f"probe_liveness_ok sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} payload={lp.hex()}"
                                )
                                print(
                                    "post-probe liveness: OK "
                                    f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} payload={lp.hex()}"
                                )
                            except Exception as le:
                                tr.note(
                                    f"probe_liveness_failed sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} "
                                    f"error={type(le).__name__}:{le}"
                                )
                                print(
                                    "post-probe liveness: FAILED "
                                    f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} "
                                    f"error={type(le).__name__}: {le}"
                                )
                        if args.probe_stop_family_on_error:
                            tr.note(f"probe_family_stop_on_error op1=0x{active_op1:02x}")
                            print(f"probe family stop-on-error: op1=0x{active_op1:02x}")
                            break
                        if not args.probe_continue_on_error:
                            # A disconnect here usually terminates further probes.
                            break
                    if args.probe_inter_send_delay > 0 and i < (len(probe_ops) - 1):
                        await asyncio.sleep(args.probe_inter_send_delay)

                tr.note(
                    f"probe_family_end op1=0x{active_op1:02x} had_error={int(family_had_error)}"
                )
                if family_had_error and not args.probe_continue_on_error:
                    break
                if args.probe_inter_send_delay > 0 and op1_idx < (len(op1_targets) - 1):
                    await asyncio.sleep(args.probe_inter_send_delay)

        if args.probe_83_select_then_02_max_idx >= 0:
            start_idx = max(0, int(args.probe_83_start_idx))
            max_idx = int(args.probe_83_select_then_02_max_idx)
            repeat_02 = max(1, int(args.probe_83_repeat_02_per_idx))
            if probe_op1 != 0x83:
                print(
                    "warning: --probe-83-select-then-02-max-idx is set but probe-op1 != 0x83; "
                    "forcing staged loop op1 to 0x83"
                )
            print(
                "probe staged mode: op1=0x83 sequence=(83,01 idx -> 83,02 idx) "
                f"idx={start_idx}..{max_idx} repeat_02={repeat_02}"
            )
            if probe_watch_subs:
                print(
                    "probe staged support-watch subs="
                    + ",".join(f"0x{x:02x}" for x in probe_watch_subs)
                )
            tr.note(
                "probe_83_staged idx="
                f"{start_idx}..{max_idx} repeat_02={repeat_02}"
            )
            for idx in range(start_idx, max_idx + 1):
                payload = int(idx).to_bytes(4, "big")
                print(f"probe staged: sending (83,01) idx={idx} payload={payload.hex()}")
                try:
                    _o1, _o2, p1 = await exchange(
                        cli,
                        tr,
                        0x83,
                        0x01,
                        payload,
                        timeout=args.probe_timeout,
                    )
                    print(
                        f"probe staged: (83,01) idx={idx} rsp_len={len(p1)} rsp={p1.hex()}"
                    )
                    if probe_watch_subs:
                        for sub in probe_watch_subs:
                            try:
                                sp = await read_support_sub(
                                    cli,
                                    tr,
                                    sub,
                                    timeout=args.probe_timeout,
                                )
                                prev = support_prev.get(sub)
                                support_prev[sub] = sp
                                changed = prev is not None and prev != sp
                                label = "first" if prev is None else ("changed" if changed else "same")
                                print(
                                    "probe support-watch: "
                                    f"after=(83,01 idx={idx}) sub=0x{sub:02x} {label} raw={sp.hex()}"
                                )
                                if changed:
                                    tr.note(
                                        "probe_support_changed "
                                        f"after=(83,01 idx={idx}) sub=0x{sub:02x} "
                                        f"prev={prev.hex()} now={sp.hex()}"
                                    )
                            except Exception as se:
                                tr.note(
                                    "probe_support_read_failed "
                                    f"after=(83,01 idx={idx}) sub=0x{sub:02x} "
                                    f"error={type(se).__name__}:{se}"
                                )
                                print(
                                    "probe support-watch: FAILED "
                                    f"after=(83,01 idx={idx}) sub=0x{sub:02x} "
                                    f"error={type(se).__name__}: {se}"
                                )

                    if args.probe_inter_send_delay > 0:
                        await asyncio.sleep(args.probe_inter_send_delay)

                    for rep in range(repeat_02):
                        print(
                            "probe staged: sending (83,02) "
                            f"idx={idx} rep={rep + 1}/{repeat_02} payload={payload.hex()}"
                        )
                        _o1, _o2, p2 = await exchange(
                            cli,
                            tr,
                            0x83,
                            0x02,
                            payload,
                            timeout=args.probe_timeout,
                        )
                        print(
                            "probe staged: (83,02) "
                            f"idx={idx} rep={rep + 1}/{repeat_02} rsp_len={len(p2)} rsp={p2.hex()}"
                        )
                        if probe_watch_subs:
                            for sub in probe_watch_subs:
                                try:
                                    sp = await read_support_sub(
                                        cli,
                                        tr,
                                        sub,
                                        timeout=args.probe_timeout,
                                    )
                                    prev = support_prev.get(sub)
                                    support_prev[sub] = sp
                                    changed = prev is not None and prev != sp
                                    label = "first" if prev is None else ("changed" if changed else "same")
                                    print(
                                        "probe support-watch: "
                                        f"after=(83,02 idx={idx} rep={rep + 1}/{repeat_02}) "
                                        f"sub=0x{sub:02x} {label} raw={sp.hex()}"
                                    )
                                    if changed:
                                        tr.note(
                                            "probe_support_changed "
                                            f"after=(83,02 idx={idx} rep={rep + 1}/{repeat_02}) "
                                            f"sub=0x{sub:02x} prev={prev.hex()} now={sp.hex()}"
                                        )
                                except Exception as se:
                                    tr.note(
                                        "probe_support_read_failed "
                                        f"after=(83,02 idx={idx} rep={rep + 1}/{repeat_02}) "
                                        f"sub=0x{sub:02x} error={type(se).__name__}:{se}"
                                    )
                                    print(
                                        "probe support-watch: FAILED "
                                        f"after=(83,02 idx={idx} rep={rep + 1}/{repeat_02}) "
                                        f"sub=0x{sub:02x} error={type(se).__name__}: {se}"
                                    )
                        if len(p2) > 1:
                            tr.note(
                                "probe_83_data_candidate "
                                f"idx={idx} rep={rep + 1} len={len(p2)} payload={p2.hex()}"
                            )
                            print(
                                "probe staged: DATA-CANDIDATE "
                                f"idx={idx} rep={rep + 1} len={len(p2)}"
                            )
                        if args.probe_inter_send_delay > 0 and rep < (repeat_02 - 1):
                            await asyncio.sleep(args.probe_inter_send_delay)
                except Exception as e:
                    tr.note(
                        f"probe_83_staged_idx_{idx}_error={type(e).__name__}:{e}"
                    )
                    print(
                        f"probe staged idx={idx} error: {type(e).__name__}: {e}"
                    )
                    if 0 <= args.probe_liveness_sub <= 0xFF:
                        try:
                            lp = await read_support_sub(
                                cli,
                                tr,
                                int(args.probe_liveness_sub) & 0xFF,
                                timeout=args.probe_timeout,
                            )
                            tr.note(
                                "probe_liveness_ok "
                                f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} payload={lp.hex()}"
                            )
                            print(
                                "post-staged liveness: OK "
                                f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} payload={lp.hex()}"
                            )
                        except Exception as le:
                            tr.note(
                                "probe_liveness_failed "
                                f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} "
                                f"error={type(le).__name__}:{le}"
                            )
                            print(
                                "post-staged liveness: FAILED "
                                f"sub=0x{(int(args.probe_liveness_sub) & 0xFF):02x} "
                                f"error={type(le).__name__}: {le}"
                            )
                    if not args.probe_continue_on_error:
                        break
                if args.probe_inter_send_delay > 0 and idx < max_idx:
                    await asyncio.sleep(args.probe_inter_send_delay)

        if args.probe_only:
            print("probe-only complete")
            tr.note("probe_only_complete")
            return 0

        print("sending 88,00")
        attempted_transfer_ops = True
        await exchange(cli, tr, 0x88, 0x00, timeout=6.0)

        if not args.skip_88_01:
            meta_payload = bytes.fromhex(args.meta_payload_hex)
            print(f"sending 88,01 payload={meta_payload.hex()}")
            _o1, _o2, p = await exchange(cli, tr, 0x88, 0x01, meta_payload, timeout=6.0)
            if len(p) >= 10:
                total = struct.unpack_from(">I", p, 1)[0]
                chunk = struct.unpack_from(">I", p, 5)[0]
                print(f"metadata decode: total={total} chunk={chunk}")

        print(f"chunk loop: count={args.chunks} start={args.start_chunk}")
        for i in range(args.chunks):
            idx = args.start_chunk + i
            payload = idx.to_bytes(4, "big")
            await send_only(cli, tr, 0x88, 0x02, payload)
            op1, op2, p = await cli.recv(timeout=args.chunk_timeout)
            tr.rx(op1, op2, p)
            print(f"RX ({op1:02x},{op2:02x}) len={len(p)} payload={p.hex()} [chunk idx={idx}]")
            if op1 == 0x88 and op2 == 0x02 and len(p) >= 5:
                jpeg_buf.extend(p[5:])

        if not args.skip_close and attempted_transfer_ops:
            await close_transfer_session(cli, tr, timeout=3.0)

        data = bytes(jpeg_buf)
        soi = data.find(b"\xff\xd8")
        eoi = data.rfind(b"\xff\xd9")
        if soi >= 0 and eoi > soi:
            jpg = data[soi:eoi + 2]
            jpg_path = out_dir / f"share_light{tag}_{model or 'unknown'}_{ts}.jpg"
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
        if not args.skip_close and attempted_transfer_ops:
            try:
                await close_transfer_session(cli, tr, timeout=2.0)
            except Exception as e:
                tr.note(f"teardown_close_unexpected={type(e).__name__}:{e}")
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
