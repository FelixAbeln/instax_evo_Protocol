"""
Find the image transfer burst and the commands immediately after it (where
print-history registration would live). Image data writes start with 0x90-0xEF.
Handshake page frames use 0xF0-0xFF. Commands use 0x80-0x8F.
Save to output file for full inspection.
"""
import struct, sys
from pathlib import Path

LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)
OUT = Path("captures/extracted/2026-05-17/timeline.txt")

WIDE_WRITE  = 0x0020
WIDE_NOTIFY = 0x001D
MINI_WRITE  = 0x002A
MINI_NOTIFY = 0x0027


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


def is_image(v):
    """Image data: first byte in 0x90-0xEF (not 0x80-0x8F command range,
    not 0xF0-0xFF handshake page range) and >14 bytes."""
    return bool(v) and len(v) > 14 and 0x90 <= v[0] <= 0xEF


# Find the image transfer burst
image_writes = [(ts, v) for ts, d, op, h, v in ALL
                if h == WIDE_WRITE and d == "host" and is_image(v)]

if image_writes:
    burst_start = image_writes[0][0]
    burst_end   = image_writes[-1][0]
    # Find the actual dense burst (where consecutive writes are < 1s apart)
    # Walk forward to find where burst starts and ends
    gaps = [(image_writes[i][0], image_writes[i-1][0])
            for i in range(1, len(image_writes))
            if image_writes[i][0] - image_writes[i-1][0] > 5.0]
    print(f"Image writes: {len(image_writes)}")
    print(f"  First: T+{burst_start-t0:.1f}s")
    print(f"  Last:  T+{burst_end-t0:.1f}s")
    print(f"  Gaps > 5s in image stream: {len(gaps)}")
    for gap_ts, prev_ts in gaps[:10]:
        print(f"    Gap at T+{prev_ts-t0:.1f}s → T+{gap_ts-t0:.1f}s ({gap_ts-prev_ts:.1f}s)")
else:
    print("No image data writes found!")

# Build full command timeline (excluding image data bulk)
events = []
for ts, d, op, h, v in ALL:
    if h == WIDE_WRITE and d == "host" and not is_image(v):
        events.append((ts, "W→cam", v))
    elif h == WIDE_NOTIFY and d == "dev":
        events.append((ts, "W←cam", v))
    elif h == MINI_WRITE and d == "host" and not is_image(v):
        events.append((ts, "M→cam", v))
    elif h == MINI_NOTIFY and d == "dev":
        events.append((ts, "M←cam", v))

events.sort(key=lambda e: e[0])

with open(OUT, "w", encoding="utf-8") as f:
    f.write(f"Image burst: T+{burst_start-t0:.1f}s → T+{burst_end-t0:.1f}s  "
            f"({len(image_writes)} writes, {len(image_writes)*19//1024}KB approx)\n\n")

    prev_ts = t0
    for ts, label, v in events:
        dt = ts - prev_ts
        gap = f"   ← gap {dt:.0f}s" if dt > 3.0 else ""
        # Skip keepalives in the middle of the session
        is_kv = (len(v) <= 3 and v and v[0] in (0x4b, 0x63, 0x4e))
        if is_kv and 15 < ts - t0 < burst_end - t0 - 30:
            prev_ts = ts
            continue
        f.write(f"T+{ts-t0:8.1f}s [{label}] {v.hex()[:64]}{gap}\n")
        prev_ts = ts

print(f"\nTimeline saved to: {OUT}  ({OUT.stat().st_size//1024}KB)")
print("Events written:", len(events))
