#!/usr/bin/env python3
"""Decode FI028 (88,01) compact metadata tails and match favorite slots.

Usage examples:
  python scripts/decode_8801_compact.py --raw 000003474e0000261532303236303632323138353433340000000000003201000000
  python scripts/decode_8801_compact.py --log path/to/console.txt --snapshot captures/favorites/snapshots/favorites_slots_20260522_200902.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

RAW_RE = re.compile(r"raw=([0-9a-fA-F]+)")


@dataclass
class ParsedMeta:
    raw_hex: str
    status: int
    total_size: int
    chunk_size: int
    timestamp_ascii: str
    timestamp_fmt: str
    tail: bytes


def parse_8801_raw(raw_hex: str) -> ParsedMeta:
    raw_hex = raw_hex.strip().lower()
    if raw_hex.startswith("0x"):
        raw_hex = raw_hex[2:]
    data = bytes.fromhex(raw_hex)
    if len(data) < 34:
        raise ValueError(f"expected >=34 bytes, got {len(data)}")

    status = data[0]
    total_size = int.from_bytes(data[1:5], "big")
    chunk_size = int.from_bytes(data[5:9], "big")
    ts_raw = data[9:23].decode("ascii", errors="replace")
    if len(ts_raw) == 14 and ts_raw.isdigit():
        ts_fmt = f"{ts_raw[0:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} {ts_raw[8:10]}:{ts_raw[10:12]}:{ts_raw[12:14]}"
    else:
        ts_fmt = ts_raw
    tail = data[23:34]

    return ParsedMeta(
        raw_hex=raw_hex,
        status=status,
        total_size=total_size,
        chunk_size=chunk_size,
        timestamp_ascii=ts_raw,
        timestamp_fmt=ts_fmt,
        tail=tail,
    )


def extract_raws_from_log(log_text: str) -> list[str]:
    return [m.group(1).lower() for m in RAW_RE.finditer(log_text)]


def mapped_tail_fields(tail: bytes) -> dict[str, int]:
    if len(tail) < 11:
        raise ValueError("tail must be 11 bytes")
    return {
        "b0": tail[1],
        "b1": tail[2],
        "b2": tail[3],
        "s2": tail[4],
        "b4": tail[6],
        "b5": tail[10],
    }


def slot_fields_from_snapshot(slot: dict) -> dict[str, int]:
    s1 = bytes.fromhex(slot["selector_01"])
    s2 = bytes.fromhex(slot["selector_02"])
    # selector_01 layout: [00,01,slot,occupied,b0,b1,b2,b3,b4,b5,b6,b7]
    return {
        "b0": s1[4],
        "b1": s1[5],
        "b2": s1[6],
        "s2": s2[4],
        "b4": s1[8],
        "b5": s1[9],
    }


def score_candidate(meta_fields: dict[str, int], slot_fields: dict[str, int]) -> tuple[int, list[str]]:
    matched: list[str] = []
    for k, v in meta_fields.items():
        if slot_fields.get(k) == v:
            matched.append(k)
    return len(matched), matched


def match_slots(meta: ParsedMeta, snapshot: dict) -> list[dict]:
    meta_f = mapped_tail_fields(meta.tail)
    out: list[dict] = []
    for row in snapshot.get("slots", []):
        sf = slot_fields_from_snapshot(row)
        score, matched = score_candidate(meta_f, sf)
        out.append({
            "slot": row.get("slot"),
            "score": score,
            "matched": matched,
            "selector_01": row.get("selector_01"),
            "selector_02": row.get("selector_02"),
        })
    out.sort(key=lambda x: (-x["score"], x["slot"]))
    return out


def summarize(meta: ParsedMeta, matches: list[dict] | None = None) -> str:
    lines: list[str] = []
    lines.append(f"raw={meta.raw_hex}")
    lines.append(
        f"status=0x{meta.status:02x} total={meta.total_size} chunk={meta.chunk_size} "
        f"timestamp={meta.timestamp_fmt}"
    )
    lines.append(f"tail={meta.tail.hex()}")
    mf = mapped_tail_fields(meta.tail)
    lines.append(
        "mapped: "
        f"b0=0x{mf['b0']:02x} b1=0x{mf['b1']:02x} b2=0x{mf['b2']:02x} "
        f"s2=0x{mf['s2']:02x} b4=0x{mf['b4']:02x} b5=0x{mf['b5']:02x}"
    )

    if matches is not None:
        best = matches[0]["score"] if matches else -1
        tied = [m for m in matches if m["score"] == best]
        tie_slots = ",".join(str(m["slot"]) for m in tied)
        lines.append(f"best_score={best}/6 slots={tie_slots}")
        for m in matches[:4]:
            lines.append(
                f"  slot {m['slot']:02d}: score={m['score']}/6 matched={','.join(m['matched'])}"
            )

    return "\n".join(lines)


def load_snapshot(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_raw_inputs(args: argparse.Namespace) -> Iterable[str]:
    if args.raw:
        for item in args.raw:
            yield item
    if args.log:
        text = args.log.read_text(encoding="utf-8", errors="replace")
        raws = extract_raws_from_log(text)
        if args.latest_only and raws:
            yield raws[-1]
        else:
            for r in raws:
                yield r


def main() -> int:
    ap = argparse.ArgumentParser(description="Decode compact settings from raw (88,01) metadata")
    ap.add_argument("--raw", action="append", help="raw hex payload from (88,01); can be repeated")
    ap.add_argument("--log", type=Path, help="console/log text containing lines like 'raw=<hex>'")
    ap.add_argument("--latest-only", action="store_true", help="with --log, decode only the last raw line")
    ap.add_argument("--snapshot", type=Path, help="favorites snapshot JSON for slot matching")
    args = ap.parse_args()

    if not args.raw and not args.log:
        ap.error("provide --raw and/or --log")

    snapshot = load_snapshot(args.snapshot) if args.snapshot else None

    raws = list(iter_raw_inputs(args))
    if not raws:
        raise SystemExit("no raw metadata lines found")

    for i, raw in enumerate(raws, start=1):
        try:
            meta = parse_8801_raw(raw)
        except Exception as e:
            print(f"[{i}] decode error: {e}")
            continue

        matches = match_slots(meta, snapshot) if snapshot else None
        print(f"[{i}]")
        print(summarize(meta, matches))
        if i != len(raws):
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
