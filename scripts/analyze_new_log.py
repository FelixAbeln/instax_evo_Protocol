"""
Analyze new btsnoop log (3 connect/disconnect sessions).
Decodes: GATT structure, handshake sequence, handles, what triggers status packets.
"""
import struct
from pathlib import Path

NEW_LOG = Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-18-40-26__btsnoop_hci.log")
OLD_LOG = Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-34-32__btsnoop_hci.log")


def parse_btsnoop(path: Path):
    """Yield (ts_sec, direction, hci_type, data) for all records"""
    with open(path, "rb") as f:
        hdr = f.read(16)
        assert hdr[:8] == b"btsnoop\x00", f"Not btsnoop: {path}"
        while True:
            rec_hdr = f.read(24)
            if len(rec_hdr) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec_hdr[:16])
            ts_usec = struct.unpack(">q", rec_hdr[16:])[0]
            ts_sec = (ts_usec - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            if not data:
                break
            direction = "dev" if flags & 1 else "host"
            hci_type = data[0]
            yield ts_sec, direction, hci_type, data


def parse_att(path: Path):
    """Yield (ts_sec, direction, att_op, att_handle, payload) for ATT packets only"""
    for ts, direction, hci_type, data in parse_btsnoop(path):
        if hci_type != 0x02 or len(data) < 10:
            continue
        cid = struct.unpack_from("<H", data, 7)[0]
        if cid != 0x0004:
            continue
        att_op = data[9]
        att_handle = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
        payload = data[12:] if len(data) > 12 else b""
        yield ts, direction, att_op, att_handle, payload


def find_sessions(path: Path):
    """Find session boundaries via MTU exchange (op 0x02 = MTU req, 0x03 = MTU resp)"""
    sessions = []
    current_start = None
    all_pkts = list(parse_att(path))
    base = all_pkts[0][0] if all_pkts else 0

    for ts, d, op, h, p in all_pkts:
        if op == 0x02:  # MTU Request (host→device = new session)
            current_start = ts
        elif op == 0x03 and current_start:  # MTU Response (device accepted)
            mtu = struct.unpack_from("<H", p, 0)[0] if len(p) >= 2 else 0
            sessions.append({"start": current_start, "mtu": mtu})
            current_start = None

    return sessions, all_pkts, base


def show_session_packets(all_pkts, base, session_start, session_end, label):
    """Print all packets in a session window"""
    print(f"\n{'='*70}")
    print(f"SESSION: {label}  (T={session_start-base:.3f}s → T={session_end-base:.3f}s)")
    print(f"{'='*70}")

    notify_pkt_map = {0x52: "WriteNoResp", 0x12: "WriteReq", 0x1B: "Notify",
                      0x1D: "Indicate", 0x02: "MTUReq", 0x03: "MTUResp",
                      0x08: "ReadByTypeReq", 0x09: "ReadByTypeResp",
                      0x10: "ReadByGrpReq", 0x11: "ReadByGrpResp",
                      0x04: "FindInfoReq", 0x05: "FindInfoResp",
                      0x01: "Error", 0x0A: "ReadReq", 0x0B: "ReadResp",
                      0x13: "WriteResp", 0x1E: "HandleValueConf",
                      0x16: "PrepWriteReq", 0x18: "ExecWriteReq"}

    in_session = False
    prev_ts = session_start
    for ts, d, op, h, p in all_pkts:
        if ts < session_start:
            continue
        if ts > session_end:
            break
        in_session = True
        dt = ts - prev_ts
        op_name = notify_pkt_map.get(op, f"0x{op:02X}")
        dir_arrow = "   -->" if d == "host" else "<--   "
        hex_p = p.hex()[:60] + ("..." if len(p) > 30 else "")
        print(f"  T+{ts-base:8.3f} ({dt:+.3f}s) {dir_arrow}  [{op_name:14s}] h=0x{h:04X}  [{len(p):3}] {hex_p}")
        prev_ts = ts


def main():
    print(f"Analyzing: {NEW_LOG.name}")
    print(f"Old log:   {OLD_LOG.name}")

    # ── New log ───────────────────────────────────────────────────────────────
    all_pkts = list(parse_att(NEW_LOG))
    if not all_pkts:
        print("No ATT packets found in new log!")
        return

    base = all_pkts[0][0]
    total_dur = all_pkts[-1][0] - base
    print(f"\nNew log: {len(all_pkts)} ATT packets over {total_dur:.1f}s")

    # Find MTU exchanges to locate sessions
    sessions = []
    for i, (ts, d, op, h, p) in enumerate(all_pkts):
        if op == 0x02 and d == "host":
            sessions.append({"start": ts, "idx": i})
        elif op == 0x03 and d == "dev" and sessions and "mtu" not in sessions[-1]:
            mtu = struct.unpack_from("<H", p, 0)[0] if len(p) >= 2 else 0
            sessions[-1]["mtu"] = mtu
            sessions[-1]["gatt_start"] = ts

    print(f"\nFound {len(sessions)} session(s) via MTU exchange:")
    for i, s in enumerate(sessions):
        print(f"  Session {i+1}: T+{s['start']-base:.3f}s  MTU={s.get('mtu','?')}")

    # Show all ATT handles seen (write targets)
    write_handles = set()
    notify_handles = set()
    for ts, d, op, h, p in all_pkts:
        if op in (0x52, 0x12) and d == "host":
            write_handles.add(h)
        if op == 0x1B and d == "dev":
            notify_handles.add(h)

    print(f"\nHost write targets (handles): {sorted(f'0x{h:04X}' for h in write_handles)}")
    print(f"Device notify handles:        {sorted(f'0x{h:04X}' for h in notify_handles)}")

    # Show full packet dump for each session
    for i, s in enumerate(sessions):
        end = sessions[i+1]["start"] - 1 if i+1 < len(sessions) else all_pkts[-1][0] + 1
        label = f"Session {i+1} (MTU={s.get('mtu','?')})"

        # Find end by looking for a gap > 60s or next session start
        session_end = end
        show_session_packets(all_pkts, base, s["start"], session_end, label)

    # ── Key: what immediately precedes status notifications? ─────────────────
    print(f"\n{'='*70}")
    print("STATUS PACKET CONTEXT (what comes just before/after 16xx/17xx notify)")
    print(f"{'='*70}")
    status_ops = []
    for i, (ts, d, op, h, p) in enumerate(all_pkts):
        if op == 0x1B and d == "dev" and len(p) == 5 and p[0] in (0x16, 0x17):
            # Show 5 packets before and 2 after
            window = all_pkts[max(0, i-5):i+3]
            status_ops.append((ts, p, window))

    if not status_ops:
        print("  No 5-byte 0x16/0x17 status packets found in this log")
        print("\n  All notify payloads from this log:")
        for ts, d, op, h, p in all_pkts:
            if op == 0x1B and d == "dev":
                print(f"    T+{ts-base:8.3f}  h=0x{h:04X}  [{len(p):2}] {p.hex()}")
    else:
        for ts, p, window in status_ops[:10]:
            print(f"\n  Status packet T+{ts-base:.3f}s: {p.hex()}")
            for wts, wd, wop, wh, wp in window:
                marker = " ***" if (wts == ts and wd == "dev" and wp == p) else ""
                dir_arrow = "  -->" if wd == "host" else "<-- "
                op_name = {0x52:"WriteNoResp", 0x1B:"Notify", 0x12:"WriteReq", 0x13:"WriteResp"}.get(wop, f"0x{wop:02X}")
                print(f"    T+{wts-base:8.3f}  {dir_arrow}  {op_name:12s} h=0x{wh:04X}  [{len(wp):3}] {wp.hex()[:40]}{marker}")


if __name__ == "__main__":
    main()
