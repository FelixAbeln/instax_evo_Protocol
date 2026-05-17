"""
Extract the JPEG image delivered by 0x82 in btsnoop session 144.

The phone sends 0x82,0x01 (pull request) as writes to h=0x0010.
The camera streams the JPEG back as ATT notifications on h=0x0012
(IOS Link framing: 61 42 [total_len:2B] 82 01 [chunk_idx:2B] [JPEG_bytes...]).

With ATT MTU=23, each notification carries at most 20 bytes of payload.
Multiple notifications are needed to deliver a full 0x82,0x01 IOS Link packet.
"""
import struct
from pathlib import Path

BASE = Path("captures/extracted/19-51-52/FS/data/log/bt")
LOG  = BASE / "btsnoop_hci.log"
OUT  = Path("captures/session144-0x82.jpg")

WRITE_OPS  = {0x52, 0x12}
NOTIFY_OPS = {0x1B, 0x1D}


def parse_btsnoop(path):
    with open(path, "rb") as f:
        f.read(16)  # file header
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
            if cid != 0x0004:   # only ATT
                continue
            att_op = data[9]
            h      = struct.unpack_from("<H", data, 10)[0]
            v      = data[12:]
            yield ts_sec, att_op, h, v


# ── Pass 1: find the 0x82 window ─────────────────────────────────────────────
t0 = None
win_start = None
win_end   = None

for ts, att_op, h, v in parse_btsnoop(LOG):
    if t0 is None:
        t0 = ts
    rel = ts - t0
    if h == 0x0010 and att_op in WRITE_OPS and len(v) >= 6:
        if v[0] == 0x41 and v[1] == 0x62 and v[4] == 0x82:
            if win_start is None:
                win_start = rel
            win_end = rel

print(f"0x82 phone-writes span: t={win_start:.2f}s – t={win_end:.2f}s")

# ── Pass 2: collect all camera notifications inside the 0x82 window ───────────
# Include a short margin at the end to catch the last IOS Link fragments.
MARGIN_END = 2.0

all_notify_bytes = bytearray()
notify_count     = 0

for ts, att_op, h, v in parse_btsnoop(LOG):
    if t0 is None:
        break
    rel = ts - t0
    if rel < win_start:
        continue
    if rel > win_end + MARGIN_END:
        break
    if h == 0x0012 and att_op in NOTIFY_OPS:
        all_notify_bytes.extend(v)
        notify_count += 1

print(f"Collected {notify_count} notifications = {len(all_notify_bytes)} raw bytes")

# ── Scan for all IOS Link packets (61 42) and reassemble 0x82,0x01 payloads ──
buf  = bytes(all_notify_bytes)
pos  = 0
jpeg = bytearray()
pkt_count = 0

while pos < len(buf) - 6:
    # Find next 61 42 magic
    magic = buf.find(b'\x61\x42', pos)
    if magic < 0:
        break
    if magic + 6 > len(buf):
        break
    total_len = struct.unpack_from(">H", buf, magic + 2)[0]
    op1 = buf[magic + 4]
    op2 = buf[magic + 5]

    pkt_end = magic + total_len
    if pkt_end > len(buf):
        # Packet extends past what we collected — partial; grab what we have
        pkt_end = len(buf)

    payload = buf[magic + 6 : pkt_end - 1]  # strip checksum

    if op1 == 0x82 and op2 == 0x01:
        pkt_count += 1
        if len(payload) >= 2:
            chunk_idx = struct.unpack_from(">H", payload, 0)[0]
            chunk_data = payload[2:]
        else:
            chunk_idx = -1
            chunk_data = payload

        if pkt_count <= 5 or pkt_count % 50 == 0:
            print(f"  pkt #{pkt_count:3d}  chunk_idx={chunk_idx:4d}  "
                  f"data[{len(chunk_data)}B]  first8={chunk_data[:8].hex()}")
        jpeg.extend(chunk_data)

    elif op1 == 0x82 and op2 in (0x00, 0x02):
        print(f"  op=(0x82,0x{op2:02x})  payload={payload.hex()}")

    pos = max(magic + 1, pkt_end)

print(f"\nTotal 0x82,0x01 IOS packets found: {pkt_count}")
print(f"Total JPEG bytes assembled: {len(jpeg)} ({len(jpeg)/1024:.1f} KB)")

# ── Find FFD8FF SOI and FFD9 EOI ─────────────────────────────────────────────
soi = bytes(jpeg).find(b'\xff\xd8\xff')
eoi = bytes(jpeg).rfind(b'\xff\xd9')

if soi >= 0:
    jpeg_data = bytes(jpeg[soi : eoi + 2] if eoi > soi else jpeg[soi:])
    print(f"JPEG SOI at offset {soi}, EOI at {eoi}, trimmed size: {len(jpeg_data)} B")
    OUT.write_bytes(jpeg_data)
    print(f"Saved → {OUT}")
else:
    print("No JPEG SOI (FFD8FF) found in assembled data — dumping raw bytes to inspect")
    # Dump first 128 bytes to see what we got
    print(f"First 128B: {bytes(jpeg[:128]).hex()}")
    # Save raw anyway
    OUT.with_suffix(".bin").write_bytes(bytes(jpeg))
    print(f"Raw dump → {OUT.with_suffix('.bin')}")
