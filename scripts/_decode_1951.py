"""
Decode the new 19-51-52 capture.
- btsnoop_hci.log.last : Android profile (h=0x0020) — likely firmware update
- btsnoop_hci.log      : Android profile continues + IOS Link (h=0x0010)

Goals:
1. Decode IOS Link protocol packets on h=0x0010 (btsnoop_hci.log)
2. Summarise Android profile commands on h=0x0020 in both files
3. Find camera→phone notifications that correspond to image readback
"""
import struct
from pathlib import Path

BASE = Path("captures/extracted/19-51-52/FS/data/log/bt")

# ── helpers ──────────────────────────────────────────────────────────────────

def parse_btsnoop(path):
    """Yield (ts_sec, flags, att_op, handle, value_bytes) for ATT packets."""
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
            if not data or data[0] != 0x02 or len(data) < 12:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            att_op = data[9]
            if len(data) < 12:
                continue
            h = struct.unpack_from("<H", data, 10)[0]
            v = data[12:]
            yield ts_sec, flags, att_op, h, v


def decode_link_packet(raw: bytes):
    """Parse IOS Link protocol framing: 41 62 [len_be2] [op1] [op2] [payload] [xor]"""
    if len(raw) < 5:
        return None
    if raw[0] != 0x41 or raw[1] != 0x62:
        return None
    total_len = struct.unpack_from(">H", raw, 2)[0]  # total including header+len field
    if len(raw) < total_len:
        return None  # truncated
    op1 = raw[4]
    op2 = raw[5] if len(raw) > 5 else 0
    payload = raw[6:total_len - 1] if total_len > 6 else b""
    xor = raw[total_len - 1]
    return op1, op2, payload, xor


def classify_android_page(page_byte: int) -> str:
    if 0x90 <= page_byte <= 0xEF:
        return "IMG_DATA"
    if 0xF0 <= page_byte <= 0xFF:
        return "COMPANION"
    if 0x80 <= page_byte <= 0x8F:
        return "CMD"
    if page_byte in (0x4B, 0x4A, 0x63):
        return "KEEPALIVE"
    return f"UNK_{page_byte:02x}"


# ── ATT opcodes ──────────────────────────────────────────────────────────────
# 0x52 = Write Command (no response), 0x12 = Write Request, 0x1B = Handle Value Notification
WRITE_OPS = {0x52, 0x12}
NOTIFY_OPS = {0x1B, 0x1D}

# ── Analyse btsnoop_hci.log (contains IOS Link on h=0x0010) ──────────────────

print("=" * 70)
print("  btsnoop_hci.log — IOS Link packets on h=0x0010")
print("=" * 70)

t0 = None
link_packets = []
android_cmds_log = []

for ts, flags, att_op, h, v in parse_btsnoop(BASE / "btsnoop_hci.log"):
    if t0 is None:
        t0 = ts
    rel = ts - t0

    if h == 0x0010 and att_op in WRITE_OPS:
        pkt = decode_link_packet(v)
        if pkt:
            op1, op2, payload, xor = pkt
            link_packets.append((rel, op1, op2, payload))
        else:
            # raw bytes if not decoded
            link_packets.append((rel, None, None, v))

    # Android Evo Wide commands (non-image)
    if h == 0x0020 and att_op in WRITE_OPS and len(v) > 0:
        page = v[0]
        cat = classify_android_page(page)
        if cat in ("CMD", "COMPANION"):
            android_cmds_log.append((rel, v.hex()))

    # Camera→phone notifications on h=0x0012 (IOS notify) or h=0x001D (Android notify)
    if h in (0x0012, 0x001D) and att_op in NOTIFY_OPS and len(v) > 4:
        pkt = decode_link_packet(v) if h == 0x0012 else None
        if pkt:
            op1, op2, payload, xor = pkt
            direction = "cam→phone IOS"
            print(f"  t={rel:8.2f}s  [{direction}]  op=({op1:#04x},{op2:#04x})  payload[{len(payload)}]={payload[:16].hex()}")

# Print decoded IOS Link writes
print(f"\nIOS Link writes (h=0x0010): {len(link_packets)} total\n")

# Group consecutive identical opcodes
prev = None
run = 0
for i, (rel, op1, op2, payload) in enumerate(link_packets):
    if op1 is None:
        print(f"  t={rel:8.2f}s  [raw, no 41 62]  {payload[:16].hex()}")
        prev = None
        run = 0
        continue
    key = (op1, op2)
    if key == prev:
        run += 1
    else:
        if run > 1:
            print(f"                ... × {run} more")
        run = 1
        is_data = (op1 == 0x10 and op2 == 0x01)
        payload_preview = payload[:12].hex() if not is_data else f"<chunk {struct.unpack_from('>H', payload, 0)[0] if len(payload) >= 2 else '?'}>"
        print(f"  t={rel:8.2f}s  op=({op1:#04x},{op2:#04x})  payload[{len(payload)}]={payload_preview}")
    prev = key

if run > 1:
    print(f"                ... × {run} more")

# Print Android commands from .log file
print(f"\n\nAndroid CMD/COMPANION writes on h=0x0020 (btsnoop_hci.log): {len(android_cmds_log)}")
for rel, hex_v in android_cmds_log[:40]:
    print(f"  t={rel:8.2f}s  {hex_v[:64]}")
if len(android_cmds_log) > 40:
    print(f"  ... {len(android_cmds_log)-40} more")

# ── Analyse btsnoop_hci.log.last (Android profile — firmware update?) ─────────

print("\n\n" + "=" * 70)
print("  btsnoop_hci.log.last — Android profile commands on h=0x0020")
print("=" * 70)

t0 = None
last_cmds = []
last_fw_writes = []

for ts, flags, att_op, h, v in parse_btsnoop(BASE / "btsnoop_hci.log.last"):
    if t0 is None:
        t0 = ts
    rel = ts - t0

    if h == 0x0020 and att_op in WRITE_OPS and len(v) > 0:
        page = v[0]
        cat = classify_android_page(page)
        if cat == "CMD":
            last_cmds.append((rel, v.hex()))
        elif cat == "IMG_DATA":
            last_fw_writes.append(rel)

print(f"\nImage/FW data writes (0x90-0xEF page): {len(last_fw_writes)}")
if last_fw_writes:
    print(f"  First: t={last_fw_writes[0]:.1f}s   Last: t={last_fw_writes[-1]:.1f}s")

print(f"\nAndroid CMD writes (0x80-0x8F page): {len(last_cmds)}")
for rel, hex_v in last_cmds[:60]:
    print(f"  t={rel:8.1f}s  {hex_v[:64]}")
if len(last_cmds) > 60:
    print(f"  ... {len(last_cmds)-60} more")
