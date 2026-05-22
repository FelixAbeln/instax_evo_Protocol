from __future__ import annotations

import argparse
import struct
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


BTSNOOP_MAGIC = b"btsnoop\x00"
TS_OFFSET_US = 0x00E03AB44A676000

ATT_WRITE_OPS = {0x12, 0x52}
ATT_NOTIFY_OPS = {0x1B, 0x1D}
WRITE_HANDLE = 0x0010
NOTIFY_HANDLE = 0x0012

PHONE = "P->C"
CAMERA = "C->P"


@dataclass
class Frame:
    ts: float
    direction: str
    op1: int
    op2: int
    payload: bytes


def iter_btsnoop_from_bytes(blob: bytes):
    if len(blob) < 16 or blob[:8] != BTSNOOP_MAGIC:
        return
    off = 16
    while off + 24 <= len(blob):
        orig_len, inc_len, flags, _drops = struct.unpack_from(">IIII", blob, off)
        ts_us = struct.unpack_from(">q", blob, off + 16)[0]
        off += 24
        if off + inc_len > len(blob):
            break
        data = blob[off : off + inc_len]
        off += inc_len

        if not data or data[0] != 0x02 or len(data) < 13:
            continue
        cid = struct.unpack_from("<H", data, 7)[0]
        if cid != 0x0004:
            continue
        att_op = data[9]
        if att_op not in ATT_WRITE_OPS | ATT_NOTIFY_OPS:
            continue
        handle = struct.unpack_from("<H", data, 10)[0]
        value = bytes(data[12:])
        direction = PHONE if (flags & 1) else CAMERA
        ts_s = (ts_us - TS_OFFSET_US) / 1_000_000
        yield ts_s, direction, att_op, handle, value


def iter_ios_frames_from_blob(blob: bytes):
    buf_p2c = bytearray()
    buf_c2p = bytearray()

    for ts, direction, att_op, handle, value in iter_btsnoop_from_bytes(blob):
        if handle == WRITE_HANDLE and att_op in ATT_WRITE_OPS:
            buf_p2c.extend(value)
            while len(buf_p2c) >= 6:
                if buf_p2c[0] != 0x41 or buf_p2c[1] != 0x62:
                    buf_p2c.clear()
                    break
                total = struct.unpack_from(">H", buf_p2c, 2)[0]
                if len(buf_p2c) < total:
                    break
                frame = bytes(buf_p2c[:total])
                del buf_p2c[:total]
                yield Frame(ts, PHONE, frame[4], frame[5], frame[6 : total - 1])
        elif handle == NOTIFY_HANDLE and att_op in ATT_NOTIFY_OPS:
            buf_c2p.extend(value)
            while len(buf_c2p) >= 6:
                if buf_c2p[0] != 0x61 or buf_c2p[1] != 0x42:
                    buf_c2p.clear()
                    break
                total = struct.unpack_from(">H", buf_c2p, 2)[0]
                if len(buf_c2p) < total:
                    break
                frame = bytes(buf_c2p[:total])
                del buf_c2p[:total]
                yield Frame(ts, CAMERA, frame[4], frame[5], frame[6 : total - 1])


def analyze_frames(frames: list[Frame]) -> dict:
    info04_values: list[bytes] = []
    info04_changes: list[bytes] = []
    shot05_values: list[int] = []
    pull_starts = 0
    pull_completes = 0
    pending_idx = None
    idx_counts: dict[int, list[int]] = {0x00: [], 0x02: []}
    idx_raws: dict[int, Counter[str]] = {0x00: Counter(), 0x02: Counter()}

    for fr in frames:
        if fr.direction == CAMERA and fr.op1 == 0x00 and fr.op2 == 0x02 and len(fr.payload) >= 2:
            sub = fr.payload[1]
            if sub == 0x04:
                info04_values.append(fr.payload)
                if not info04_changes or info04_changes[-1] != fr.payload:
                    info04_changes.append(fr.payload)
            elif sub == 0x05 and len(fr.payload) >= 6:
                shot05_values.append(fr.payload[5])

        if fr.direction == PHONE and fr.op1 == 0x84 and fr.op2 == 0x09 and len(fr.payload) >= 1:
            pending_idx = fr.payload[0]

        if fr.direction == CAMERA and fr.op1 == 0x84 and fr.op2 == 0x09 and pending_idx is not None:
            if len(fr.payload) >= 14 and pending_idx in (0x00, 0x02):
                count = struct.unpack_from(">I", fr.payload, 10)[0]
                idx_counts[pending_idx].append(count)
                idx_raws[pending_idx][fr.payload.hex()] += 1
            pending_idx = None

        if fr.direction == PHONE and fr.op1 == 0x88 and fr.op2 == 0x00:
            pull_starts += 1
        if fr.direction == CAMERA and fr.op1 == 0x88 and fr.op2 == 0x05 and fr.payload[:1] == b"\x00":
            pull_completes += 1

    def stats(vals: list[int]) -> str:
        if not vals:
            return "none"
        return f"min={min(vals)} max={max(vals)} unique={sorted(set(vals))} n={len(vals)}"

    out = {
        "frames": len(frames),
        "pull_starts": pull_starts,
        "pull_completes": pull_completes,
        "info04_changes": [v.hex() for v in info04_changes],
        "shot05_stats": stats(shot05_values),
        "idx00_stats": stats(idx_counts[0x00]),
        "idx02_stats": stats(idx_counts[0x02]),
        "idx00_top_raw": idx_raws[0x00].most_common(2),
        "idx02_top_raw": idx_raws[0x02].most_common(2),
    }
    return out


def iter_btsnoop_sources(root: Path):
    for d in [p for p in root.iterdir() if p.is_dir()]:
        for p in d.rglob("btsnoop_hci.log*"):
            try:
                yield f"DIR:{d.name}:{p.relative_to(d)}", p.read_bytes()
            except Exception:
                continue

    for z in [p for p in root.glob("*.zip") if p.is_file()]:
        try:
            with zipfile.ZipFile(z, "r") as zf:
                for name in zf.namelist():
                    lname = name.lower()
                    if lname.endswith("btsnoop_hci.log") or lname.endswith("btsnoop_hci.log.last"):
                        try:
                            yield f"ZIP:{z.name}:{name}", zf.read(name)
                        except Exception:
                            continue
        except Exception:
            continue


def main():
    ap = argparse.ArgumentParser(description="Analyze Phone Link bugreport recordings for queue/pull signals")
    ap.add_argument("--root", default=r"c:\Users\Compf\Downloads\Phone Link")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"not found: {root}")

    rows = []
    for idx, (label, blob) in enumerate(iter_btsnoop_sources(root), start=1):
        frames = list(iter_ios_frames_from_blob(blob))
        if not frames:
            continue
        rows.append((label, analyze_frames(frames)))
        if len(rows) >= args.limit:
            break

    if not rows:
        print("No btsnoop sources decoded")
        return

    print(f"Decoded {len(rows)} btsnoop source(s)\n")
    for label, a in rows:
        print(f"=== {label} ===")
        print(f"frames={a['frames']} pull_starts={a['pull_starts']} pull_completes={a['pull_completes']}")
        print(f"info04 changes ({len(a['info04_changes'])}):")
        full_info04 = (a["pull_starts"] > 0 or a["pull_completes"] > 0)
        shown = a["info04_changes"] if full_info04 else a["info04_changes"][:4]
        for v in shown:
            print(f"  {v}")
        if not full_info04 and len(a["info04_changes"]) > 4:
            print("  ...")
        print(f"shot05: {a['shot05_stats']}")
        print(f"84,09 idx00: {a['idx00_stats']}")
        print(f"84,09 idx02: {a['idx02_stats']}")
        if a["idx00_top_raw"]:
            print(f"idx00 top raw: {a['idx00_top_raw'][0][0]} x{a['idx00_top_raw'][0][1]}")
        if a["idx02_top_raw"]:
            print(f"idx02 top raw: {a['idx02_top_raw'][0][0]} x{a['idx02_top_raw'][0][1]}")
        print()


if __name__ == "__main__":
    main()
