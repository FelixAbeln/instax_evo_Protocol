"""
Analyze status message structure to find image count and battery fields.
Compares packets across multiple sessions to identify changing fields.
"""
import struct
from pathlib import Path

LOGS = [
    Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-34-32__btsnoop_hci.log"),
    Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-43-18__btsnoop_hci.log"),
    Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-52-45__btsnoop_hci.log"),
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

            if not data or data[0] != 0x02:
                continue
            if len(data) < 10:
                continue

            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue

            att_op = data[9]
            direction = "device->host" if flags & 1 else "host->device"
            att_handle = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
            payload = data[12:] if len(data) > 12 else b""

            yield ts_sec, direction, att_op, att_handle, payload


def session_relative(packets):
    """Convert to relative timestamps from first packet"""
    if not packets:
        return []
    t0 = packets[0][0]
    return [(ts - t0,) + rest for ts, *rest in packets]


def decode_early_setup_packets():
    """
    Analyze the first few seconds of each session to find image count field.
    The protocol.md notes that early packets at 3.5-5.5s contain '02 01' and '02 02'
    which track image count transitioning from 2->1.
    """
    for log_path in LOGS:
        if not log_path.exists():
            continue

        label = log_path.stem[:60]
        packets = list(parse_btsnoop(log_path))
        if not packets:
            continue

        t0 = packets[0][0]

        # Get all notify packets from device
        notify = [
            (ts - t0, h, p)
            for ts, d, op, h, p in packets
            if op == 0x1B and d == "device->host"
        ]

        print(f"\n{'=' * 80}")
        print(f"SESSION: {label}")
        print(f"  Total notify packets: {len(notify)}")

        # Look at the very first few packets - GATT discovery/setup phase
        print("\n  --- First 20 notify packets (setup phase) ---")
        print(f"  {'T(s)':>8}  {'Handle':>6}  {'Len':>4}  Payload hex")
        for ts, h, p in notify[:20]:
            # Annotate 02 XX patterns
            hints = []
            for i in range(len(p) - 1):
                if p[i] == 0x02:
                    hints.append(f"+{i}:02{p[i+1]:02x}")
            hint_str = " ".join(hints)
            print(f"  {ts:8.3f}  0x{h:04X}  {len(p):>4}  {p.hex():<40}  {hint_str}")

        # Find packets with exactly 5 bytes (matching the '02 01 02 02' pattern noted)
        short_packets = [(ts, h, p) for ts, h, p in notify if len(p) == 5]
        if short_packets:
            print(f"\n  --- 5-byte notify packets (likely image count messages) ---")
            for ts, h, p in short_packets:
                print(f"  {ts:8.3f}  0x{h:04X}  {p.hex()}  -> bytes: {list(p)}")

        # Find all packets matching pattern: starts with same prefix, differs by one byte
        # Look for the image count field
        print(f"\n  --- Looking for 02 01 / 02 02 patterns ---")
        for ts, h, p in notify[:50]:
            for i in range(len(p) - 1):
                if p[i] == 0x02 and p[i + 1] in (0x01, 0x02, 0x03, 0x00):
                    print(f"  T={ts:7.3f}  h=0x{h:04X}  [{i}]=02 {p[i+1]:02x}  full={p.hex()}")
                    break


def analyze_battery_field():
    """
    From protocol.md: battery level is 0-3 pips (full=3).
    Look for bytes that could encode this across sessions.
    The first capture was at full battery.
    """
    print("\n\n" + "=" * 80)
    print("BATTERY FIELD ANALYSIS")
    print("=" * 80)
    print("If battery = full (3 pips), look for 0x03 in consistent positions")
    print()

    for log_path in LOGS:
        if not log_path.exists():
            continue

        label = log_path.stem[-30:]
        packets = list(parse_btsnoop(log_path))
        t0 = packets[0][0] if packets else 0

        notify = [
            (ts - t0, h, p)
            for ts, d, op, h, p in packets
            if op == 0x1B and d == "device->host"
        ]

        # Get first 5 notifications - these are during GATT discovery
        # Look for consistent structure
        print(f"\n  {label} - First 5 notify payloads:")
        for ts, h, p in notify[:5]:
            if len(p) >= 13:
                # Look at key offsets
                b10 = f"{p[10]:02x}" if len(p) > 10 else "--"
                b11 = f"{p[11]:02x}" if len(p) > 11 else "--"
                b12 = f"{p[12]:02x}" if len(p) > 12 else "--"
                print(f"    T={ts:6.3f}  h=0x{h:04X}  [10]={b10} [11]={b11} [12]={b12}  full={p.hex()}")
            else:
                print(f"    T={ts:6.3f}  h=0x{h:04X}  (short: {len(p)} bytes) {p.hex()}")


def find_image_count():
    """
    Image count should change between sessions:
    - Session 1 (17:34:32): 2 images loaded → 1 after sending
    - Session 2 (17:43:18): unknown state
    - Session 3 (17:52:45): keep-alive phase (no transfer?)
    
    Find the byte that differs between sessions in the status messages.
    """
    print("\n\n" + "=" * 80)
    print("IMAGE COUNT FIELD ISOLATION")
    print("=" * 80)
    print()

    all_first_packets = []

    for log_path in LOGS:
        if not log_path.exists():
            continue

        label = log_path.stem[-20:]
        packets = list(parse_btsnoop(log_path))
        t0 = packets[0][0] if packets else 0

        notify = [
            (ts - t0, h, p)
            for ts, d, op, h, p in packets
            if op == 0x1B and d == "device->host"
        ]

        # Get the very first notify packet - should have initial state
        if notify:
            ts, h, p = notify[0]
            print(f"  {label}: first notify = {p.hex()}")
            all_first_packets.append((label, p))

    print()
    # Find which bytes differ between sessions
    if len(all_first_packets) >= 2:
        min_len = min(len(p) for _, p in all_first_packets)
        print(f"  Comparing first {min_len} bytes across sessions:")
        print(f"  {'Offset':>6}  " + "  ".join(f"{label[-8:]:>10}" for label, _ in all_first_packets) + "  Same?")
        print("  " + "-" * 70)
        for i in range(min_len):
            vals = [p[i] for _, p in all_first_packets]
            same = "✓" if len(set(vals)) == 1 else "← DIFFERS"
            val_str = "  ".join(f"0x{v:02X}  " for v in vals)
            print(f"  [{i:>3}]    {val_str}  {same if same == 'same' else 'DIFFERS'}")


if __name__ == "__main__":
    decode_early_setup_packets()
    analyze_battery_field()
    find_image_count()
