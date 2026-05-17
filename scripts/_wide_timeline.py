"""
Full Evo Wide session timeline — find the print trigger and any post-print commands.
Only show writes that look like commands (not raw image pixel chunks).
Also show all camera notifications.
"""
import struct
from pathlib import Path

LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)

WIDE_WRITE  = 0x0020
WIDE_NOTIFY = 0x001D
# Mini Evo handles (second session, for reference)
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

# ----- Identify image-data runs (consecutive large writes on h=0x0020) -------
# Group writes by "silent period" gaps > 2s or short writes
wide_writes = [(ts, v) for ts, d, op, h, v in ALL
               if h == WIDE_WRITE and d == "host" and op in (0x52, 0x12)]

# Mark each write as "image" or "command"
# Image data: first byte in 0x80-0xFF range AND > 14 bytes
IMAGE_FIRST_BYTES = set(range(0x80, 0x100))
# Exclude known command first bytes
COMMAND_FIRST_BYTES_ALWAYS = {0x00, 0x4b, 0x4e, 0x4f, 0x50, 0x51, 0x52, 0x53,
                               0x60, 0x61, 0x62, 0x63, 0x64, 0x65}

def is_image(v):
    if not v:
        return False
    if len(v) < 15:
        return False
    # Very short writes are commands
    return v[0] in IMAGE_FIRST_BYTES and v[0] not in COMMAND_FIRST_BYTES_ALWAYS

# Find the image transfer window
image_start = None
image_end = None
image_count = 0
for ts, v in wide_writes:
    if is_image(v):
        image_count += 1
        if image_start is None:
            image_start = ts
        image_end = ts

print(f"Image data writes: {image_count}")
print(f"Image transfer: T+{image_start-t0:.1f}s → T+{image_end-t0:.1f}s  "
      f"(duration={image_end-image_start:.1f}s)")

# ----- Show all non-image events in timeline --------------------------------
print("\n" + "=" * 72)
print("FULL SESSION TIMELINE (commands + notifications, no image data)")
print("=" * 72)
print("  [host→cam] on h=0x0020 (Evo Wide write)")
print("  [cam→host] on h=0x001D (Evo Wide notify)")
print("  [M-host]   on h=0x002A (Mini Evo write)")
print("  [M-cam]    on h=0x0027 (Mini Evo notify)")
print()

events = []
for ts, d, op, h, v in ALL:
    if h == WIDE_WRITE and d == "host" and not is_image(v):
        events.append((ts, "host→cam", v))
    elif h == WIDE_NOTIFY and d == "dev":
        events.append((ts, "cam→host", v))
    elif h == MINI_WRITE and d == "host" and not is_image(v):
        events.append((ts, "M-host  ", v))
    elif h == MINI_NOTIFY and d == "dev":
        events.append((ts, "M-cam   ", v))

events.sort(key=lambda e: e[0])

prev_ts = t0
for ts, label, v in events:
    dt = ts - prev_ts
    gap = f" *** gap {dt:.1f}s ***" if dt > 2.0 else ""
    print(f"  T+{ts-t0:8.1f}s  [{label}]  {v.hex()[:56]}{gap}")
    prev_ts = ts

print(f"\n  (image data: T+{image_start-t0:.1f}s–{image_end-t0:.1f}s, {image_count} writes)")
