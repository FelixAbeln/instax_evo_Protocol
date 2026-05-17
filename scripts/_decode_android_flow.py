"""
Decode the Android BLE protocol print session from btsnoop.
Focus on command opcodes (h=0x002A writes and h=0x002A/0x0027 notifications)
to find commands we haven't implemented yet, especially print-history registration.
"""
import struct
from pathlib import Path
from collections import defaultdict

LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)

# Known Android protocol command bytes (first byte of write payload on h=0x002A)
CMD_NAMES = {
    0x00: "HANDSHAKE_AUTH",      # 00 05 [DEVICE_ID 8B] 00 00
    0x16: "CMD",                 # 16 XX = command
    0x17: "CMD_RESP?",           # 17 XX = response variant?
    0x19: "KEEPALIVE_27",
    0x1B: "KEEPALIVE_1D",
}
CMD16_NAMES = {
    0x00: "POLL_INIT",
    0x01: "BATTERY",
    0x02: "FILM_COUNT",
    0x03: "UNKNOWN_03",
    0x04: "UNKNOWN_04",
    0x05: "UNKNOWN_05",
    0x10: "PRINT_START?",
    0x11: "PRINT_DATA?",
    0x12: "PRINT_END?",
    0x13: "PRINT_TRIGGER?",
    0x14: "PRINT_HISTORY?",
    0x20: "UNKNOWN_20",
    0x21: "UNKNOWN_21",
}


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
    """Yield (ts, direction, att_op, handle, value) for ATT packets."""
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


# Main data channel handles (from scan)
WRITE_MAIN   = 0x0020   # bulk image data
WRITE_CTRL   = 0x002A   # control/auth commands
WRITE_EXTRA  = 0x0025   # new — unknown
NOTIFY_MAIN  = 0x001D   # notifications from h=0x0020 channel
NOTIFY_CTRL  = 0x0027   # notifications from h=0x002A channel

all_att = list(iter_att(LOG))
print(f"Total ATT packets: {len(all_att)}")

# ── Section 1: all non-bulk writes on control channel (h=0x002A) ─────────────
print("\n" + "=" * 80)
print(f"CONTROL CHANNEL WRITES (h=0x{WRITE_CTRL:04X}) — first byte != 0x00 (skip auth init)")
print("=" * 80)

ctrl_writes = [(ts, v) for ts, d, op, h, v in all_att
               if h == WRITE_CTRL and d == "host" and op in (0x52, 0x12) and len(v) > 0]

base = all_att[0][0] if all_att else 0
seen_cmds = set()

for ts, v in ctrl_writes:
    first = v[0]
    if first == 0x00:
        continue   # skip auth init handshake (very long)
    sub = v[1] if len(v) > 1 else 0
    name = ""
    if first == 0x16:
        name = CMD16_NAMES.get(sub, f"CMD_0x{sub:02X}")
    else:
        name = CMD_NAMES.get(first, f"0x{first:02X}")
    key = (first, sub)
    marker = "" if key in seen_cmds else "  ← first occurrence"
    seen_cmds.add(key)
    print(f"  T+{ts-base:8.1f}s  {v.hex()[:48]:50s}  {name}{marker}")

# ── Section 2: all notifications from camera (h=0x0027 and h=0x001D) ─────────
print("\n" + "=" * 80)
print("CAMERA NOTIFICATIONS (h=0x001D and h=0x0027) — excluding keepalives")
print("=" * 80)

notif = [(ts, h, v) for ts, d, op, h, v in all_att
         if h in (NOTIFY_MAIN, NOTIFY_CTRL) and d == "dev" and op == 0x1B]

seen_notifs = set()
for ts, h, v in notif:
    if not v:
        continue
    # Skip keepalive pings (19/1B 00 XX)
    if len(v) == 3 and v[0] in (0x19, 0x1B) and v[1] == 0x00:
        continue
    key = v[:2]
    marker = "" if key in seen_notifs else "  ← first occurrence"
    seen_notifs.add(key)
    print(f"  T+{ts-base:8.1f}s  h=0x{h:04X}  {v.hex()[:48]:50s}{marker}")

# ── Section 3: unknown extra channel h=0x0025 ────────────────────────────────
print("\n" + "=" * 80)
print(f"EXTRA CHANNEL (h=0x{WRITE_EXTRA:04X}) — all writes")
print("=" * 80)

extra = [(ts, v) for ts, d, op, h, v in all_att
         if h == WRITE_EXTRA and d == "host" and op in (0x52, 0x12)]
for ts, v in extra[:50]:
    print(f"  T+{ts-base:8.1f}s  {v.hex()[:64]}")
if len(extra) > 50:
    print(f"  ... ({len(extra)-50} more)")

# ── Section 4: timeline of unique command opcodes in order ───────────────────
print("\n" + "=" * 80)
print("UNIQUE COMMAND SEQUENCE (first occurrence of each opcode)")
print("=" * 80)

seen = set()
for ts, v in ctrl_writes:
    if len(v) < 2:
        continue
    key = (v[0], v[1])
    if key not in seen:
        seen.add(key)
        first, sub = key
        if first == 0x16:
            name = CMD16_NAMES.get(sub, f"CMD_0x{sub:02X}")
        else:
            name = CMD_NAMES.get(first, f"byte0=0x{first:02X}")
        print(f"  T+{ts-base:8.1f}s  {v.hex()[:32]:35s}  {name}")
