"""
Deep-decode the 0x82 (history download) window in btsnoop_hci.log.
Shows ALL packets (writes + notifications) in the 0x82 window,
including camera→phone responses on h=0x0012.
"""
import struct
from pathlib import Path

BASE = Path("captures/extracted/19-51-52/FS/data/log/bt")
LOG = BASE / "btsnoop_hci.log"

WRITE_OPS = {0x52, 0x12}
NOTIFY_OPS = {0x1B, 0x1D}


def parse_btsnoop(path):
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
            h = struct.unpack_from("<H", data, 10)[0]
            v = data[12:]
            yield ts_sec, att_op, h, v


def decode_link(raw: bytes):
    if len(raw) < 6 or raw[0] != 0x41 or raw[1] != 0x62:
        return None
    total_len = struct.unpack_from(">H", raw, 2)[0]
    if len(raw) < total_len:
        total_len = len(raw)
    op1 = raw[4]
    op2 = raw[5]
    payload = raw[6:total_len - 1] if total_len > 7 else b""
    return op1, op2, payload


def decode_link_cam(raw: bytes):
    """Camera→phone uses 61 42 framing."""
    if len(raw) < 6 or raw[0] != 0x61 or raw[1] != 0x42:
        return None
    total_len = struct.unpack_from(">H", raw, 2)[0]
    if len(raw) < total_len:
        total_len = len(raw)
    op1 = raw[4]
    op2 = raw[5]
    payload = raw[6:total_len - 1] if total_len > 7 else b""
    return op1, op2, payload


# ── First pass: find t0 and the 0x82 window ───────────────────────────────────
t0 = None
win_start = None
win_end = None

for ts, att_op, h, v in parse_btsnoop(LOG):
    if t0 is None:
        t0 = ts
    rel = ts - t0
    if h == 0x0010 and att_op in WRITE_OPS:
        pkt = decode_link(v)
        if pkt and pkt[0] == 0x82:
            if win_start is None:
                win_start = rel
            win_end = rel

print(f"0x82 window: t={win_start:.2f}s → t={win_end:.2f}s (span={win_end-win_start:.2f}s)")

# Also find the 0x84,0x09 / 0x0a / 0x0b windows
new_84_start = None
for ts, att_op, h, v in parse_btsnoop(LOG):
    rel = ts - t0
    if h == 0x0010 and att_op in WRITE_OPS:
        pkt = decode_link(v)
        if pkt and pkt[0] == 0x84 and pkt[1] in (0x09, 0x0a, 0x0b):
            if new_84_start is None:
                new_84_start = rel
                print(f"First 0x84,0x{pkt[1]:02x} at t={rel:.2f}s  payload={pkt[2].hex()}")
                break

# ── Second pass: dump everything in the 0x82 window ±30s ────────────────────
MARGIN = 30.0
win_lo = win_start - MARGIN
win_hi = win_end + MARGIN

print(f"\nAll packets in window t={win_lo:.1f}s → t={win_hi:.1f}s\n")

chunk_sizes = []
cam_notify_count = 0
cam_notify_sizes = []

for ts, att_op, h, v in parse_btsnoop(LOG):
    rel = ts - t0
    if rel < win_lo:
        continue
    if rel > win_hi:
        break

    if h == 0x0010 and att_op in WRITE_OPS:
        pkt = decode_link(v)
        if pkt:
            op1, op2, payload = pkt
            if op1 == 0x82:
                if op2 == 0x01:
                    chunk_sizes.append(len(payload))
                    if len(chunk_sizes) <= 3 or len(chunk_sizes) % 50 == 0:
                        idx = struct.unpack_from(">H", payload, 0)[0] if len(payload) >= 2 else "?"
                        print(f"  t={rel:8.2f}s  →cam  op=(0x82,0x01) chunk#{idx}  payload[{len(payload)}]  data[{len(payload)-2}B]  first8={payload[2:10].hex()}")
                    continue
                else:
                    print(f"  t={rel:8.2f}s  →cam  op=(0x82,0x{op2:02x})  payload[{len(payload)}]={payload.hex()}")
            elif op1 in (0x80, 0x84, 0x00):
                print(f"  t={rel:8.2f}s  →cam  op=(0x{op1:02x},0x{op2:02x})  payload[{len(payload)}]={payload[:12].hex()}")

    elif h == 0x0012 and att_op in NOTIFY_OPS:
        # IOS notify characteristic (camera→phone)
        pkt = decode_link_cam(v)
        cam_notify_count += 1
        cam_notify_sizes.append(len(v))
        if pkt:
            op1, op2, payload = pkt
            if len(cam_notify_sizes) <= 5 or op2 not in (0x01,) or cam_notify_count % 50 == 0:
                print(f"  t={rel:8.2f}s  cam→  op=(0x{op1:02x},0x{op2:02x})  payload[{len(payload)}]={payload[:12].hex()}")
        else:
            print(f"  t={rel:8.2f}s  cam→  [raw, no 61 42]  v[{len(v)}]={v[:16].hex()}")

print(f"\n--- Summary ---")
if chunk_sizes:
    print(f"0x82,0x01 chunks: {len(chunk_sizes)}")
    print(f"  chunk payload sizes: min={min(chunk_sizes)} max={max(chunk_sizes)} avg={sum(chunk_sizes)/len(chunk_sizes):.0f}")
    print(f"  total data bytes: {sum(chunk_sizes)} ({sum(chunk_sizes)//1024}KB)")
print(f"cam→phone notifications in window: {cam_notify_count}")
if cam_notify_sizes:
    print(f"  notify sizes: min={min(cam_notify_sizes)} max={max(cam_notify_sizes)}")
