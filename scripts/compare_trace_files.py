from __future__ import annotations

import argparse
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class TraceLine:
    direction: str
    op1: int
    op2: int
    payload: bytes


def parse_line(line: str) -> TraceLine:
    parts = line.strip().lstrip("\ufeff").split()
    if len(parts) < 3:
        raise ValueError(f"bad trace line: {line!r}")
    direction = parts[0]
    op1 = int(parts[1], 16)
    op2 = int(parts[2], 16)
    payload_hex = parts[3] if len(parts) >= 4 else ""
    return TraceLine(direction, op1, op2, bytes.fromhex(payload_hex) if payload_hex else b"")


def load_trace(path: Path) -> list[TraceLine]:
    return [parse_line(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def byte_diff(left: bytes, right: bytes) -> str:
    diffs: list[str] = []
    for idx, (lb, rb) in enumerate(zip(left, right)):
        if lb != rb:
            diffs.append(f"{idx}:{lb:02x}->{rb:02x}")
    if len(left) != len(right):
        diffs.append(f"len:{len(left)}->{len(right)}")
    return ", ".join(diffs) if diffs else "(no changes)"


def fmt(trace: TraceLine | None) -> str:
    if trace is None:
        return "<missing>"
    return f"{trace.direction} {trace.op1:02x} {trace.op2:02x} {trace.payload.hex()}"


def signature(trace: TraceLine) -> tuple[str, int, int]:
    return (trace.direction, trace.op1, trace.op2)


def format_idx(idx: int | None) -> str:
    return "-" if idx is None else str(idx)


def emit_index_compare(official: list[TraceLine], observed: list[TraceLine]) -> None:
    max_len = max(len(official), len(observed)) if official or observed else 0
    print(f"official_lines={len(official)} observed_lines={len(observed)}")
    for idx in range(max_len):
        left = official[idx] if idx < len(official) else None
        right = observed[idx] if idx < len(observed) else None
        if left == right:
            continue
        print(f"\nIDX {idx}")
        print(f"  OFFICIAL: {fmt(left)}")
        print(f"  OBSERVED: {fmt(right)}")
        if left is not None and right is not None:
            if signature(left) != signature(right):
                print("  sig-diff: direction/opcode mismatch")
            print(f"  payload-diff: {byte_diff(left.payload, right.payload)}")


def emit_aligned_compare(official: list[TraceLine], observed: list[TraceLine]) -> None:
    official_sigs = [signature(item) for item in official]
    observed_sigs = [signature(item) for item in observed]
    matcher = SequenceMatcher(a=official_sigs, b=observed_sigs, autojunk=False)

    matched = 0
    payload_mismatches = 0
    inserts = 0
    deletes = 0

    print(f"official_lines={len(official)} observed_lines={len(observed)}")
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for off_idx, obs_idx in zip(range(i1, i2), range(j1, j2)):
                matched += 1
                off_line = official[off_idx]
                obs_line = observed[obs_idx]
                if off_line.payload != obs_line.payload:
                    payload_mismatches += 1
                    print(f"\nPAYLOAD MISMATCH off={off_idx} obs={obs_idx} sig={fmt_signature(off_line)}")
                    print(f"  OFFICIAL: {fmt(off_line)}")
                    print(f"  OBSERVED: {fmt(obs_line)}")
                    print(f"  payload-diff: {byte_diff(off_line.payload, obs_line.payload)}")
            continue

        print(f"\n{tag.upper()} official[{i1}:{i2}] observed[{j1}:{j2}]")
        if tag in {"replace", "delete"}:
            deletes += i2 - i1
        if tag in {"replace", "insert"}:
            inserts += j2 - j1

        max_span = max(i2 - i1, j2 - j1)
        for offset in range(max_span):
            off_idx = i1 + offset if i1 + offset < i2 else None
            obs_idx = j1 + offset if j1 + offset < j2 else None
            off_line = official[off_idx] if off_idx is not None else None
            obs_line = observed[obs_idx] if obs_idx is not None else None
            print(
                f"  off={format_idx(off_idx):>4} obs={format_idx(obs_idx):>4} | "
                f"OFFICIAL: {fmt(off_line)} | OBSERVED: {fmt(obs_line)}"
            )
            if off_line is not None and obs_line is not None and signature(off_line) == signature(obs_line):
                print(f"    payload-diff: {byte_diff(off_line.payload, obs_line.payload)}")

    print(
        "\nSUMMARY "
        f"matched_signatures={matched} payload_mismatches={payload_mismatches} "
        f"official_only={deletes} observed_only={inserts}"
    )


def fmt_signature(trace: TraceLine) -> str:
    return f"{trace.direction} {trace.op1:02x} {trace.op2:02x}"


def emit_manual_dump(official: list[TraceLine], observed: list[TraceLine], limit: int | None) -> None:
    max_len = max(len(official), len(observed)) if official or observed else 0
    if limit is not None:
        max_len = min(max_len, limit)
    print(f"official_lines={len(official)} observed_lines={len(observed)} dump_lines={max_len}")
    for idx in range(max_len):
        left = official[idx] if idx < len(official) else None
        right = observed[idx] if idx < len(observed) else None
        left_len = len(left.payload) if left is not None else 0
        right_len = len(right.payload) if right is not None else 0
        print(
            f"IDX {idx}\n"
            f"  OFFICIAL[{idx if left is not None else '-'}] len={left_len}: {fmt(left)}\n"
            f"  OBSERVED[{idx if right is not None else '-'}] len={right_len}: {fmt(right)}"
        )
        if left is not None and right is not None:
            print(f"  same-signature: {signature(left) == signature(right)}")
            print(f"  payload-diff: {byte_diff(left.payload, right.payload)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two message-only trace files byte-by-byte")
    ap.add_argument("official", type=Path)
    ap.add_argument("observed", type=Path)
    ap.add_argument(
        "--mode",
        choices=("index", "align", "manual"),
        default="align",
        help="Comparison mode: raw index-by-index, signature-aligned, or full manual side-by-side dump",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional line limit for manual mode",
    )
    args = ap.parse_args()

    official = load_trace(args.official)
    observed = load_trace(args.observed)

    if args.mode == "index":
        emit_index_compare(official, observed)
    elif args.mode == "manual":
        emit_manual_dump(official, observed, args.limit)
    else:
        emit_aligned_compare(official, observed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())