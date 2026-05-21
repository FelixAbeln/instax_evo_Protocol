from __future__ import annotations

import argparse
import struct
from collections import Counter
from pathlib import Path

WRITE_OPS = {0x12, 0x52}
NOTIFY_OPS = {0x1B, 0x1D}


def parse_btsnoop(path: Path):
    with path.open("rb") as f:
        f.read(16)
        t0 = None
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            _orig_len, inc_len, _flags, _drops = struct.unpack(">IIII", rec[:16])
            ts_us = struct.unpack(">q", rec[16:])[0]
            ts_sec = (ts_us - 0x00E03AB44A676000) / 1_000_000
            if t0 is None:
                t0 = ts_sec
            data = f.read(inc_len)
            if not data or data[0] != 0x02 or len(data) < 12:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            att_op = data[9]
            handle = struct.unpack_from("<H", data, 10)[0]
            value = data[12:]
            yield ts_sec - t0, att_op, handle, value


def decode_link_from_phone(v: bytes):
    if len(v) >= 6 and v[0:2] == b"\x41\x62":
        total = struct.unpack_from(">H", v, 2)[0]
        total = min(total, len(v))
        return v[4], v[5], v[6:total - 1] if total > 7 else b""
    return None


def decode_link_from_cam(v: bytes):
    if len(v) >= 6 and v[0:2] == b"\x61\x42":
        total = struct.unpack_from(">H", v, 2)[0]
        total = min(total, len(v))
        return v[4], v[5], v[6:total - 1] if total > 7 else b""
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze bugreport btsnoop ATT traffic")
    ap.add_argument("log", type=Path)
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    rows = list(parse_btsnoop(args.log))
    if not rows:
        print("No ATT rows parsed")
        return 1

    print(f"rows={len(rows)} span={rows[-1][0]:.1f}s file={args.log}")

    handle_counts = Counter(h for _, _, h, _ in rows)
    print("\nTop handles:")
    for h, c in handle_counts.most_common(12):
        print(f"  h=0x{h:04x}  count={c}")

    link_phone = Counter()
    link_cam = Counter()
    android_pairs = Counter()

    events = []
    for t, att_op, h, v in rows:
        if att_op in WRITE_OPS:
            dec = decode_link_from_phone(v)
            if dec:
                op1, op2, payload = dec
                link_phone[(op1, op2)] += 1
                if op1 in {0x00, 0x80, 0x82, 0x84, 0x85, 0x88, 0x20}:
                    events.append((t, f"LINK write h=0x{h:04x} op=({op1:02x},{op2:02x}) len={len(payload)} p={payload[:10].hex()}"))
            elif len(v) >= 2:
                a0, a1 = v[0], v[1]
                android_pairs[(a0, a1)] += 1
                if h in {0x002A, 0x0020, 0x0014, 0x0010}:
                    events.append((t, f"RAW write h=0x{h:04x} b0b1={a0:02x} {a1:02x} len={len(v)} v={v[:12].hex()}"))

        if att_op in NOTIFY_OPS:
            dec = decode_link_from_cam(v)
            if dec:
                op1, op2, payload = dec
                link_cam[(op1, op2)] += 1
                if op1 in {0x00, 0x80, 0x82, 0x84, 0x85, 0x88, 0x20}:
                    events.append((t, f"LINK note  h=0x{h:04x} op=({op1:02x},{op2:02x}) len={len(payload)} p={payload[:10].hex()}"))
            elif len(v) >= 2 and h in {0x0027, 0x001D, 0x0012, 0x0016}:
                events.append((t, f"RAW note  h=0x{h:04x} b0b1={v[0]:02x} {v[1]:02x} len={len(v)} v={v[:12].hex()}"))

    print("\nLink phone op counts:")
    for (op1, op2), c in sorted(link_phone.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  ({op1:02x},{op2:02x}) x{c}")

    print("\nLink cam op counts:")
    for (op1, op2), c in sorted(link_cam.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  ({op1:02x},{op2:02x}) x{c}")

    print("\nRaw b0/b1 write pairs:")
    for (a0, a1), c in sorted(android_pairs.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        print(f"  {a0:02x} {a1:02x} x{c}")

    print("\nTimeline (filtered):")
    for t, msg in sorted(events, key=lambda x: x[0])[: args.limit]:
        print(f"  t={t:8.2f}s  {msg}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
