"""
Focused decoder for Instax Evo status message types.

From analysis:
- 5-byte packets: [type] [field1] [field2] [field3] [field4]
  - type=0x16/0x17: different channels (0x0027/0x001D)
  - Example: 16 01 00 03 44 -> type, count?, ?, battery?, checksum?
  - Example: 16 02 01 02 02 -> type, [2=img_count], [1=?], [02], [02=img_count again?]
- 3-byte keep-alive: [msg_id] [00] [counter]
  - 19 00 56, 19 00 57 ... incrementing sequence
"""
import struct
from pathlib import Path

LOGS = [
    ("17:34:32", Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-34-32__btsnoop_hci.log")),
    ("17:43:18", Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-43-18__btsnoop_hci.log")),
    ("17:52:45", Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-52-45__btsnoop_hci.log")),
]


def parse_btsnoop(path: Path):
    with open(path, "rb") as f:
        hdr = f.read(16)
        assert hdr[:8] == b"btsnoop\x00"
        while True:
            rec_hdr = f.read(24)
            if len(rec_hdr) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec_hdr[:16])
            ts_usec = struct.unpack(">q", rec_hdr[16:])[0]
            ts_sec = (ts_usec - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            if not data or data[0] != 0x02 or len(data) < 10:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            att_op = data[9]
            direction = "device->host" if flags & 1 else "host->device"
            att_handle = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
            payload = data[12:] if len(data) > 12 else b""
            yield ts_sec, direction, att_op, att_handle, payload


def analyze():
    for label, log_path in LOGS:
        if not log_path.exists():
            continue
        packets = list(parse_btsnoop(log_path))
        if not packets:
            continue
        t0 = packets[0][0]

        notify = [
            (ts - t0, h, p)
            for ts, d, op, h, p in packets
            if op == 0x1B and d == "device->host"
        ]

        print(f"\n{'='*70}")
        print(f"SESSION {label}  ({len(notify)} notify packets)")
        print(f"{'='*70}")

        # === 5-byte packets ===
        five_byte = [(ts, h, p) for ts, h, p in notify if len(p) == 5]
        if five_byte:
            print("\n  5-byte status packets:")
            print(f"  {'T(s)':>8}  h       b0    b1    b2    b3    b4   Interpretation")
            print("  " + "-"*70)
            for ts, h, p in five_byte:
                b = list(p)
                interp = ""
                # Pattern 16 01 00 03 XX or 17 01 00 03 XX
                if b[0] in (0x16, 0x17) and b[1] == 0x01:
                    interp = f"INIT: battery_level={b[3]} (of 3 pips)"
                # Pattern 16 02 XX 02 02 or similar
                elif b[0] in (0x16, 0x17) and b[1] == 0x02:
                    interp = f"IMG_COUNT: images_queued={b[2]}, extra={b[3]:02x} {b[4]:02x}"
                print(f"  {ts:8.3f}  0x{h:04X}  {b[0]:02x}    {b[1]:02x}    {b[2]:02x}    {b[3]:02x}    {b[4]:02x}   {interp}")

        # === 3-byte keep-alive / ping packets ===
        three_byte = [(ts, h, p) for ts, h, p in notify if len(p) == 3]
        if three_byte:
            print("\n  3-byte keep-alive/ping packets:")
            print(f"  {'T(s)':>8}  h       b0    b1    b2   Interpretation")
            print("  " + "-"*60)
            for ts, h, p in three_byte:
                b = list(p)
                interp = ""
                if b[1] == 0x00:
                    interp = f"PING: msg_id=0x{b[0]:02x}  seq={b[2]} (0x{b[2]:02x})"
                elif b[1] == 0x02:
                    interp = f"STATUS_SHORT: msg_id=0x{b[0]:02x} type=0x{b[1]:02x} val={b[2]}"
                print(f"  {ts:8.3f}  0x{h:04X}  {b[0]:02x}    {b[1]:02x}    {b[2]:02x}   {interp}")

        # === 6-byte packets ===
        six_byte = [(ts, h, p) for ts, h, p in notify if len(p) == 6]
        if six_byte:
            print("\n  6-byte status packets:")
            for ts, h, p in six_byte[:10]:
                b = list(p)
                print(f"  {ts:8.3f}  0x{h:04X}  {p.hex()}  bytes={b}")

        # === Repeated identical 13-byte packets (session start) ===
        thirteen_byte = [(ts, h, p) for ts, h, p in notify if len(p) == 13]
        if thirteen_byte:
            print("\n  13-byte session init packets:")
            for ts, h, p in thirteen_byte[:4]:
                b = list(p)
                # Try to decode as structured
                print(f"  {ts:8.3f}  0x{h:04X}  {p.hex()}")
                print(f"             bytes: {b}")
                # Last byte is always 01 in sample - could be image count
                print(f"             last_byte={b[-1]} (image count?), byte[4]={b[4]:02x} byte[5]={b[5]:02x} byte[6]={b[6]:02x}")


def compare_across_sessions():
    """Find bytes that differ between the 3 sessions"""
    print(f"\n\n{'='*70}")
    print("CROSS-SESSION COMPARISON")
    print("Look for bytes encoding image count or connection state")
    print(f"{'='*70}")

    session_first = {}
    for label, log_path in LOGS:
        if not log_path.exists():
            continue
        packets = list(parse_btsnoop(log_path))
        t0 = packets[0][0] if packets else 0
        notify = [(ts-t0, h, p) for ts, d, op, h, p in packets if op == 0x1B and d == "device->host"]
        
        # Find 5-byte packets specifically - they seem to carry count/battery
        five_byte = [(ts, h, p) for ts, h, p in notify if len(p) == 5]
        session_first[label] = five_byte[:4] if five_byte else []

    print()
    for label, pkts in session_first.items():
        print(f"  {label}: 5-byte packets:")
        for ts, h, p in pkts:
            print(f"    {ts:7.3f}  0x{h:04X}  {p.hex()}  -> {list(p)}")
        if not pkts:
            print("    (none)")


if __name__ == "__main__":
    analyze()
    compare_across_sessions()
