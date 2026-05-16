"""
Decode ATT Notify packets from btsnoop log.
Focuses on image count and battery status fields.
"""
import struct
import sys
from pathlib import Path

LOG = Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-34-32__btsnoop_hci.log")

def parse_btsnoop(path: Path):
    """Yield (ts_sec, direction, att_op, att_handle, payload) for ATT packets"""
    with open(path, "rb") as f:
        hdr = f.read(16)
        assert hdr[:8] == b"btsnoop\x00", "Not a btsnoop file"

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
                continue  # ATT only

            att_op = data[9]
            direction = "device->host" if flags & 1 else "host->device"
            att_handle = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
            payload = data[12:] if len(data) > 12 else b""

            yield ts_sec, direction, att_op, att_handle, payload


def main():
    packets = list(parse_btsnoop(LOG))

    # Show all Notify packets (op 0x1B) from device
    notify = [(ts, h, p) for ts, d, op, h, p in packets if op == 0x1B and d == "device->host"]
    print(f"Total Notify packets from device: {len(notify)}")
    print()

    # Group by handle
    handles = set(h for _, h, _ in notify)
    print(f"Notify handles: {[hex(h) for h in sorted(handles)]}")
    print()

    # Show all unique payload prefixes to identify status message format
    print("=== All Notify payloads (first 80 packets) ===")
    print(f"{'T(s)':>8}  {'Handle':>6}  {'Len':>4}  Payload hex")
    print("-" * 80)
    for ts, h, p in notify[:80]:
        print(f"{ts:8.3f}  0x{h:04X}  {len(p):>4}  {p.hex()}")

    print()

    # Focus on 0xA8 status messages specifically
    a8_packets = [(ts, h, p) for ts, h, p in notify if p and p[0] == 0xA8]
    print(f"=== 0xA8 status messages: {len(a8_packets)} total ===")
    print()

    if a8_packets:
        print("Decoding 0xA8 structure: A8 [seq] 00 [type] [data...]")
        print(f"{'T(s)':>8}  {'seq':>4}  {'type':>4}  {'Bytes 4+':40}  Annotation")
        print("-" * 100)

        for ts, h, p in a8_packets:
            seq = p[1] if len(p) > 1 else "?"
            typ = f"0x{p[3]:02X}" if len(p) > 3 else "?"
            rest = p[4:].hex() if len(p) > 4 else ""
            
            annotation = ""
            if len(p) > 4:
                # Look for 02 XX patterns
                for i in range(len(p) - 1):
                    if p[i] == 0x02:
                        annotation += f" [offset {i}: 02 {p[i+1]:02X}]"
            
            print(f"{ts:8.3f}  {seq:>4}  {typ:>4}  {rest[:40]:40}  {annotation}")

    print()

    # Show transition packets around typical image send times (3-8 seconds)
    transfer_window = [(ts, h, p) for ts, h, p in notify if 3.0 <= ts <= 8.0]
    print(f"=== Setup/transfer window (3-8s): {len(transfer_window)} packets ===")
    print(f"{'T(s)':>8}  {'Handle':>6}  Payload hex")
    print("-" * 80)
    for ts, h, p in transfer_window:
        print(f"{ts:8.3f}  0x{h:04X}  {p.hex()}")


if __name__ == "__main__":
    main()
