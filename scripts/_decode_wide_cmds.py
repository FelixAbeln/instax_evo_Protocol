"""
Decode the Evo Wide (h=0x0020) command sequence from the Android BLE capture.
The Evo Wide uses DEVICE_ID 01 00 00 00 00 00 00 00.
We want to find commands sent before/after image transfer, especially
anything that registers the print in the camera's internal history.
"""
import struct
from pathlib import Path
from collections import defaultdict

LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)

WIDE_WRITE = 0x0020    # Evo Wide: all writes go here
WIDE_NOTIFY = 0x001D   # Evo Wide: all notifications come here


def parse_btsnoop_raw(path):
    with open(path, "rb") as f:
        f.read(16)
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec[:16])
            ts_us = struct.unpack(">q", rec[16:])[0]
            ts_sec = (ts_us - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            yield ts_sec, flags, data


def iter_att(path):
    for ts, flags, data in parse_btsnoop_raw(path):
        if not data or data[0] != 0x02 or len(data) < 10:
            continue
        cid = struct.unpack_from("<H", data, 7)[0]
        if cid != 0x0004:
            continue
        direction = "dev" if flags & 1 else "host"
        att_op = data[9]
        handle = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
        value = data[12:] if len(data) > 12 else b""
        yield ts, direction, att_op, handle, value


ALL = list(iter_att(LOG))
t0 = ALL[0][0] if ALL else 0

# Split Evo Wide traffic into "command/short" vs "image data"
# Image data chunks are large (typically 900 bytes or multiple BLE writes of raw pixel data)
# Commands are short structured packets

wide_host = [(ts, v) for ts, d, op, h, v in ALL
             if h == WIDE_WRITE and d == "host" and op in (0x52, 0x12)]
wide_notif = [(ts, v) for ts, d, op, h, v in ALL
              if h == WIDE_NOTIFY and d == "dev" and op == 0x1B]

print(f"Evo Wide host writes:    {len(wide_host)}")
print(f"Evo Wide notifications:  {len(wide_notif)}")

# Classify writes by first byte
from collections import Counter
first_bytes = Counter(v[0] for _, v in wide_host if v)
print("\nFirst-byte distribution on h=0x0020:")
for b, count in first_bytes.most_common():
    print(f"  0x{b:02X}  {count:6d}  {count/len(wide_host)*100:.1f}%")

# Show all non-image writes (i.e., NOT raw pixel data)
# Raw JPEG data: first byte rarely 0x00 and has specific patterns
# Auth/cmd: start with 0x00, 0x4b, 0xf7, 0xf8...
# Let's exclude obvious image runs: bytes starting with anything in the high 0xC0+ range
# Actually, let's just filter by: short writes OR known command first-bytes

COMMAND_FIRST_BYTES = {0x00, 0x4b, 0xf7, 0xf8, 0xf9, 0xfa, 0xfb, 0xfc, 0xfd, 0xfe, 0xff,
                       0x4e, 0x4f, 0x50, 0x51, 0x52, 0x53}

print("\n" + "=" * 70)
print("ALL WRITES on h=0x0020 (short ≤40 bytes or command pattern):")
print("=" * 70)

# Find the image transfer window (when lots of consecutive writes happen)
# Show all writes that look like commands (short, or known prefixes)
image_run = 0
prev_ts = t0
for ts, v in wide_host:
    dt = ts - prev_ts
    prev_ts = ts
    is_short = len(v) <= 40
    first = v[0] if v else 0
    # Skip suspected image bytes (large runs of medium-length writes)
    if is_short or first in (0x00, 0x4b, 0x4e, 0x4f):
        print(f"  T+{ts-t0:8.1f}s  len={len(v):4d}  {v.hex()[:60]}")

print("\n" + "=" * 70)
print("ALL NOTIFICATIONS from Evo Wide (h=0x001D):")
print("=" * 70)
for ts, v in wide_notif[:200]:
    if len(v) <= 3 and v and v[0] == 0x4b:
        continue  # skip keepalive echoes
    print(f"  T+{ts-t0:8.1f}s  len={len(v):4d}  {v.hex()[:60]}")
