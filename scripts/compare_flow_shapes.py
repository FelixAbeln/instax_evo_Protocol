from __future__ import annotations

import argparse
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LinkEvent:
    direction: str
    op1: int
    op2: int
    payload: bytes


@dataclass(frozen=True)
class AndroidEvent:
    t: float
    kind: str  # W or N
    a0: int
    a1: int
    length: int


def parse_link_trace(path: Path) -> list[LinkEvent]:
    out: list[LinkEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip().split()
        if len(s) < 3:
            continue
        direction, op1, op2 = s[:3]
        payload = bytes.fromhex(s[3]) if len(s) > 3 and s[3] else b""
        out.append(LinkEvent(direction, int(op1, 16), int(op2, 16), payload))
    return out


def extract_link_transfer_segment(events: list[LinkEvent]) -> list[LinkEvent]:
    start = next(
        i
        for i, e in enumerate(events)
        if e.direction == "TX" and e.op1 == 0x82 and e.op2 == 0x10
    )
    end = next(
        i
        for i, e in enumerate(events[start:], start=start)
        if e.direction == "RX" and e.op1 == 0x82 and e.op2 == 0x22
    )
    return events[start : end + 1]


def link_phase_tokens(seg: list[LinkEvent]) -> list[str]:
    tokens: list[str] = []
    for e in seg:
        if e.direction == "TX" and (e.op1, e.op2) == (0x82, 0x10):
            tokens.append("CTRL_REQ")
        elif e.direction == "RX" and (e.op1, e.op2) == (0x82, 0x10):
            tokens.append("CTRL_ACK")
        elif e.direction == "TX" and (e.op1, e.op2) == (0x82, 0x20):
            tokens.append("CTRL_REQ")
        elif e.direction == "RX" and (e.op1, e.op2) == (0x82, 0x20):
            tokens.append("CTRL_ACK")
        elif e.direction == "TX" and (e.op1, e.op2) == (0x82, 0x21):
            tokens.append("DATA_REQ")
        elif e.direction == "RX" and (e.op1, e.op2) == (0x82, 0x21):
            tokens.append("DATA_ACK")
        elif e.direction == "TX" and (e.op1, e.op2) == (0x82, 0x22):
            tokens.append("CTRL_REQ")
        elif e.direction == "RX" and (e.op1, e.op2) == (0x82, 0x22):
            tokens.append("CTRL_ACK")
    return tokens


def parse_android_log(path: Path, min_prefix: int = 0x90) -> list[AndroidEvent]:
    events: list[AndroidEvent] = []
    with path.open("rb") as f:
        f.read(16)
        t0 = None
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            _orig_len, inc_len, _flags, _drops = struct.unpack(">IIII", rec[:16])
            ts = struct.unpack(">q", rec[16:])[0]
            data = f.read(inc_len)
            if not data or data[0] != 0x02 or len(data) < 13:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue

            att_op = data[9]
            handle = struct.unpack_from("<H", data, 10)[0]
            value = data[12:]
            if len(value) < 2:
                continue

            if t0 is None:
                t0 = ts
            t = (ts - t0) / 1_000_000

            if (
                handle == 0x0020
                and att_op in {0x12, 0x52}
                and value[0] >= min_prefix
                and value[0] <= 0x9F
            ):
                events.append(AndroidEvent(t, "W", value[0], value[1], len(value)))

            if (
                handle == 0x001D
                and att_op in {0x1B, 0x1D}
                and (value[0] >= min_prefix and value[0] <= 0x9F)
            ):
                events.append(AndroidEvent(t, "N", value[0], value[1], len(value)))

    events.sort(key=lambda e: e.t)
    return events


def cluster_android(events: list[AndroidEvent], gap_sec: float) -> list[list[AndroidEvent]]:
    if not events:
        return []
    out: list[list[AndroidEvent]] = []
    cur = [events[0]]
    for e in events[1:]:
        if e.t - cur[-1].t > gap_sec:
            out.append(cur)
            cur = [e]
        else:
            cur.append(e)
    out.append(cur)
    return out


def android_phase_tokens(cluster: list[AndroidEvent]) -> list[str]:
    tokens: list[str] = []
    for e in cluster:
        if e.kind == "W" and e.length == 2:
            tokens.append("CTRL_REQ")
        elif e.kind == "N" and e.length <= 2:
            tokens.append("CTRL_ACK")
        elif e.kind == "W" and (e.length >= 80 or e.length == 20):
            tokens.append("DATA_REQ")
        elif e.kind == "N" and e.length in {15, 17, 30, 31, 34, 48}:
            tokens.append("DATA_ACK")
        else:
            tokens.append("DATA_ACK")
    return tokens


def compress_tokens(tokens: list[str]) -> list[str]:
    if not tokens:
        return []
    out = [tokens[0]]
    for t in tokens[1:]:
        if t != out[-1]:
            out.append(t)
    return out


def summarize(name: str, tokens: list[str]) -> None:
    c = Counter(tokens)
    print(f"{name}: total={len(tokens)} ctrl_req={c['CTRL_REQ']} ctrl_ack={c['CTRL_ACK']} data_req={c['DATA_REQ']} data_ack={c['DATA_ACK']}")
    print(f"{name} compressed: {' -> '.join(compress_tokens(tokens))}")


def macro_signature(tokens: list[str]) -> tuple[int, int, int]:
    data_idxs = [i for i, t in enumerate(tokens) if t in {"DATA_REQ", "DATA_ACK"}]
    if not data_idxs:
        return len(tokens), 0, 0
    first = data_idxs[0]
    last = data_idxs[-1]
    pre = sum(1 for t in tokens[:first] if t in {"CTRL_REQ", "CTRL_ACK"})
    data = sum(1 for t in tokens[first : last + 1] if t in {"DATA_REQ", "DATA_ACK"})
    post = sum(1 for t in tokens[last + 1 :] if t in {"CTRL_REQ", "CTRL_ACK"})
    return pre, data, post


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare flow shapes between Link transfer and Android raw bursts")
    ap.add_argument("--link-trace", type=Path, default=Path("captures/trace_compare/official_flash_to_transfer.trace"))
    ap.add_argument("--android-log", type=Path, default=Path("captures/bugreport_2026-05-20/FS_data_log_bt_btsnoop_hci.log"))
    ap.add_argument("--gap", type=float, default=2.0, help="Gap seconds to split Android clusters")
    ap.add_argument("--pick-cluster", type=int, default=-1, help="Pick Android cluster index (-1 means largest likely transfer by score)")
    args = ap.parse_args()

    link_events = parse_link_trace(args.link_trace)
    link_seg = extract_link_transfer_segment(link_events)
    link_tokens = link_phase_tokens(link_seg)
    summarize("link", link_tokens)

    android_events = parse_android_log(args.android_log)
    clusters = cluster_android(android_events, args.gap)
    if not clusters:
        print("android: no clusters")
        return 1

    scored: list[tuple[int, int, list[AndroidEvent]]] = []
    for i, cl in enumerate(clusters):
        tks = android_phase_tokens(cl)
        c = Counter(tks)
        score = c["DATA_REQ"] + c["DATA_ACK"]
        scored.append((score, i, cl))

    if args.pick_cluster >= 0:
        idx = args.pick_cluster
        if idx >= len(clusters):
            raise SystemExit(f"cluster index out of range: {idx}")
        chosen = clusters[idx]
        chosen_idx = idx
    else:
        scored.sort(key=lambda x: x[0], reverse=True)
        _score, chosen_idx, chosen = scored[0]

    t0 = chosen[0].t
    t1 = chosen[-1].t
    print(f"android cluster chosen: idx={chosen_idx} window={t0:.2f}s..{t1:.2f}s dur={t1 - t0:.2f}s events={len(chosen)}")
    android_tokens = android_phase_tokens(chosen)
    summarize("android", android_tokens)

    link_shape = compress_tokens(link_tokens)
    android_shape = compress_tokens(android_tokens)
    same_shape = link_shape == android_shape
    print(f"shape_equal={same_shape}")

    lp, ld, lpost = macro_signature(link_tokens)
    ap, ad, apost = macro_signature(android_tokens)
    print(f"link macro pre_ctrl={lp} data={ld} post_ctrl={lpost}")
    print(f"android macro pre_ctrl={ap} data={ad} post_ctrl={apost}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
