"""
Identify all cameras in this capture by device ID, time range, and handles.
Then decode the Evo Wide command sequence to find print-history registration.
"""
import struct
from pathlib import Path

LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)


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

# ── 1. Find all auth-init patterns (00 05 XX XX XX XX XX XX XX XX 00 00) ─────
print("=" * 70)
print("AUTH INIT PACKETS (0x0005 = session init)")
print("=" * 70)
for ts, d, op, h, v in ALL:
    if d == "host" and len(v) >= 4 and v[0] == 0x00 and v[1] in (0x05, 0x00) and len(v) >= 10:
        dev_id = v[2:10].hex()
        print(f"  T+{ts-t0:7.1f}s  h=0x{h:04X}  op=0x{op:02X}  v={v[:14].hex()}  device_id={dev_id}")

# ── 2. Show keepalive patterns per handle ─────────────────────────────────────
print("\n" + "=" * 70)
print("KEEPALIVE PATTERNS (short writes, first byte 0x4a/0x4b/0x63/0x65)")
print("=" * 70)
seen_kv = set()
for ts, d, op, h, v in ALL:
    if len(v) >= 1 and v[0] in (0x4a, 0x4b, 0x63, 0x65):
        key = (h, v[0])
        if key not in seen_kv:
            seen_kv.add(key)
            print(f"  h=0x{h:04X}  {d:4s}  byte0=0x{v[0]:02X}  example={v.hex()}")

# ── 3. Find IOS Link profile handles (41 62 or 61 42 framing) ────────────────
print("\n" + "=" * 70)
print("IOS LINK PROTOCOL (41 62 / 61 42 framing)")
print("=" * 70)
found_link = False
for ts, d, op, h, v in ALL:
    if len(v) >= 2 and v[:2] in (b'\x41\x62', b'\x61\x42'):
        found_link = True
        print(f"  T+{ts-t0:7.1f}s  h=0x{h:04X}  {d}  {v.hex()[:40]}")
if not found_link:
    print("  (none found — this capture is Android-profile only)")

# ── 4. GATT handles that received CCCD enable ─────────────────────────────────
print("\n" + "=" * 70)
print("CCCD ENABLES (0x0100 written = subscribe to notifications)")
print("=" * 70)
for ts, d, op, h, v in ALL:
    if d == "host" and v == b'\x01\x00':
        print(f"  T+{ts-t0:7.1f}s  h=0x{h:04X} (CCCD)  → notify at h=0x{h-1:04X}")

# ── 5. Identify each camera's handle cluster ──────────────────────────────────
print("\n" + "=" * 70)
print("ALL WRITE HANDLES — time range and count")
print("=" * 70)
from collections import defaultdict
handle_times = defaultdict(list)
for ts, d, op, h, v in ALL:
    if d == "host" and op in (0x52, 0x12):
        handle_times[h].append(ts)
for h, times in sorted(handle_times.items()):
    print(f"  h=0x{h:04X}  count={len(times):6d}  "
          f"first=T+{times[0]-t0:6.1f}s  last=T+{times[-1]-t0:6.1f}s")
