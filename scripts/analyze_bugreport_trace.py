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
    ap.add_argument(
        "--raw-min-prefix",
        type=lambda s: int(s, 0),
        default=0x90,
        help="Minimum first-byte prefix to include in Android raw burst summaries (default: 0x90)",
    )
    ap.add_argument(
        "--raw-pair-window",
        type=float,
        default=1.0,
        help="Max seconds to search for nearest write->notify raw pairing (default: 1.0)",
    )
    ap.add_argument(
        "--raw-cluster-gap",
        type=float,
        default=2.0,
        help="Idle gap in seconds used to split Android raw burst windows (default: 2.0)",
    )
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
    raw_writes: list[tuple[float, int, int, int, bytes]] = []
    raw_notes: list[tuple[float, int, int, int, bytes]] = []

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
                raw_writes.append((t, h, a0, a1, v))
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
                raw_notes.append((t, h, v[0], v[1], v))
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

    # Android legacy framing helper summary.
    min_prefix = args.raw_min_prefix & 0xFF
    burst_writes = [w for w in raw_writes if w[2] >= min_prefix]
    burst_notes = [n for n in raw_notes if n[2] >= min_prefix or n[2] == 0x5A]
    if burst_writes or burst_notes:
        print("\nAndroid raw burst summary:")
        print(
            f"  min_prefix=0x{min_prefix:02x} "
            f"writes={len(burst_writes)} notes={len(burst_notes)}"
        )

        keepalive = [n for n in raw_notes if n[2] == 0x5A and n[3] == 0x00 and len(n[4]) >= 3]
        if keepalive:
            seqs = [n[4][2] for n in keepalive]
            gaps = [keepalive[i + 1][0] - keepalive[i][0] for i in range(len(keepalive) - 1)]
            avg_gap = sum(gaps) / len(gaps) if gaps else 0.0
            print(
                f"  keepalive(5a00): count={len(keepalive)} "
                f"seq_first=0x{seqs[0]:02x} seq_last=0x{seqs[-1]:02x} avg_gap={avg_gap:.2f}s"
            )

        print("\n  write->notify nearest pairs:")
        for wt, _wh, wa0, wa1, _wv in burst_writes:
            cand = None
            best_dt = None
            for nt, _nh, na0, na1, nv in raw_notes:
                if nt < wt:
                    continue
                dt = nt - wt
                if dt > args.raw_pair_window:
                    break
                # Match within same high nibble family (9x groups in observed logs).
                if (na0 >> 4) != (wa0 >> 4):
                    continue
                if best_dt is None or dt < best_dt:
                    best_dt = dt
                    cand = (nt, na0, na1, len(nv))
            if cand is None:
                print(f"    W {wa0:02x} {wa1:02x} -> (no notify within {args.raw_pair_window:.2f}s)")
                continue
            _nt, na0, na1, nlen = cand
            plus1 = (na0 == wa0) and (na1 == ((wa1 + 1) & 0xFF))
            tag = "ACK+1" if plus1 else "OTHER"
            print(
                f"    W {wa0:02x} {wa1:02x} -> N {na0:02x} {na1:02x} "
                f"nlen={nlen:3d} {tag}"
            )

        burst_events: list[tuple[float, str, int, int, int]] = []
        for t, _h, a0, a1, v in burst_writes:
            burst_events.append((t, "W", a0, a1, len(v)))
        for t, _h, a0, a1, v in burst_notes:
            burst_events.append((t, "N", a0, a1, len(v)))
        burst_events.sort(key=lambda x: x[0])

        clusters: list[list[tuple[float, str, int, int, int]]] = []
        if burst_events:
            cur = [burst_events[0]]
            for e in burst_events[1:]:
                if e[0] - cur[-1][0] > args.raw_cluster_gap:
                    clusters.append(cur)
                    cur = [e]
                else:
                    cur.append(e)
            clusters.append(cur)

        if clusters:
            print("\n  burst windows:")
            for idx, cl in enumerate(clusters, start=1):
                t_start = cl[0][0]
                t_end = cl[-1][0]
                ws = [r for r in cl if r[1] == "W"]
                ns = [r for r in cl if r[1] == "N"]
                if not ws:
                    continue
                w20 = sum(1 for r in ws if r[4] == 20)
                w_big = sum(1 for r in ws if r[4] >= 80)
                w_ctrl = sum(1 for r in ws if r[4] == 2)
                n_small = sum(1 for r in ns if r[4] <= 2)

                ack_plus1 = 0
                for wt, _wk, wa0, wa1, _wl in ws:
                    cand = [
                        n for n in ns
                        if n[0] >= wt and (n[0] - wt) <= args.raw_pair_window and (n[2] >> 4) == (wa0 >> 4)
                    ]
                    if not cand:
                        continue
                    if cand[0][2] == wa0 and cand[0][3] == ((wa1 + 1) & 0xFF):
                        ack_plus1 += 1

                likely_transfer = (w_big >= 1 and ack_plus1 >= 2)
                status = "LIKELY_TRANSFER" if likely_transfer else "maybe-control"
                print(
                    f"    #{idx} t={t_start:8.2f}-{t_end:8.2f}s dur={t_end - t_start:5.2f}s "
                    f"W={len(ws):2d} N={len(ns):2d} W20={w20:2d} Wbig={w_big:2d} "
                    f"Wctrl2={w_ctrl:2d} Nsmall2={n_small:2d} ACK+1={ack_plus1:2d} {status}"
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
