"""Quick scan of new btsnoop log to understand capture structure."""
import struct
from pathlib import Path

LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)


def parse_btsnoop_raw(path):
    with open(path, "rb") as f:
        f.read(16)  # skip file header
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec[:16])
            ts_us = struct.unpack(">q", rec[16:])[0]
            ts_sec = (ts_us - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            yield ts_sec, flags, data


handles = {}
connections = []

for ts, flags, data in parse_btsnoop_raw(LOG):
    if not data:
        continue

    # LE Connection Complete events
    if data[0] == 0x04 and len(data) >= 5:
        if data[1] == 0x3E and data[3] == 0x01 and len(data) >= 15:
            addr = ":".join(f"{b:02X}" for b in reversed(data[9:15]))
            conn_handle = struct.unpack_from("<H", data, 5)[0] if len(data) > 6 else 0
            connections.append((ts, addr, conn_handle))
            print(f"LE Conn  t={ts:8.1f}s  handle=0x{conn_handle:04X}  addr={addr}")

    # ATT packets
    if data[0] != 0x02 or len(data) < 10:
        continue
    cid = struct.unpack_from("<H", data, 7)[0]
    if cid != 0x0004:
        continue
    att_op = data[9]
    if att_op not in (0x52, 0x12) or len(data) < 12:
        continue
    h = struct.unpack_from("<H", data, 10)[0]
    v = data[12:]
    if h not in handles:
        handles[h] = {"count": 0, "samples": []}
    handles[h]["count"] += 1
    if len(handles[h]["samples"]) < 3:
        handles[h]["samples"].append(v[:20].hex())

print(f"\nWrite handles ({len(handles)}):")
for h, info in sorted(handles.items()):
    print(f"  h=0x{h:04X}  writes={info['count']:5d}  samples={info['samples']}")

print(f"\nTotal LE connections found: {len(connections)}")
