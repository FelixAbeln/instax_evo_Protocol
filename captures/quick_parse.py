"""Quick parse: dump 82xx/84xx/88xx events from btsnoop logs."""
import struct, sys
from pathlib import Path

BTSNOOP_MAGIC = b"btsnoop\x00"
TS_OFFSET_US  = 0x00E03AB44A676000
ATT_WRITE_OPS = {0x12, 0x52}
ATT_NOTIFY_OPS = {0x1B, 0x1D}
WRITE_HANDLE   = 0x0010
NOTIFY_HANDLE  = 0x0012
PHONE  = "P->C"
CAMERA = "C->P"

OP_LABELS = {
    (0x82, 0x00): "LV_START",
    (0x82, 0x02): "LV_END",
    (0x82, 0x10): "IMG_HIST_QUERY",
    (0x82, 0x20): "IMG_HIST_POLL",
    (0x82, 0x21): "IMG_HIST_CHUNK",
    (0x82, 0x22): "IMG_HIST_END",
    (0x84, 0x00): "HIST_INIT",
    (0x84, 0x01): "HIST_SCHED",
    (0x84, 0x02): "HIST_COMMIT",
    (0x84, 0x09): "HIST_LIST_REQ",
    (0x84, 0x0a): "HIST_THUMB",
    (0x84, 0x0b): "HIST_SLOT",
    (0x88, 0x00): "PULL_QUERY",
    (0x88, 0x01): "PULL_READY",
    (0x88, 0x02): "PULL_CHUNK",
    (0x88, 0x03): "PULL_ACK",
    (0x88, 0x04): "PULL_DONE",
    (0x88, 0x05): "PULL_CLOSE",
}

def iter_btsnoop(path):
    with open(path, "rb") as f:
        if f.read(8) != BTSNOOP_MAGIC:
            return
        f.read(8)  # version + datalink type
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", hdr[:16])
            ts_us = struct.unpack(">q", hdr[16:])[0]
            ts_s  = (ts_us - TS_OFFSET_US) / 1_000_000
            data  = f.read(inc_len)
            if len(data) < 10 or data[0] != 0x02:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            att_op = data[9]
            if att_op not in ATT_WRITE_OPS | ATT_NOTIFY_OPS:
                continue
            if len(data) < 13:
                continue
            handle = struct.unpack_from("<H", data, 10)[0]
            value  = bytes(data[12:])
            direction = PHONE if (flags & 1) else CAMERA
            yield ts_s, direction, att_op, handle, value

def iter_ios_frames(path):
    buf_p2c = bytearray()
    buf_c2p = bytearray()
    for ts, direction, att_op, handle, value in iter_btsnoop(path):
        if handle == WRITE_HANDLE and att_op in ATT_WRITE_OPS:
            buf_p2c.extend(value)
            while len(buf_p2c) >= 6:
                if buf_p2c[0] != 0x41 or buf_p2c[1] != 0x62:
                    buf_p2c.clear(); break
                total = struct.unpack_from(">H", buf_p2c, 2)[0]
                if len(buf_p2c) < total:
                    break
                frame = bytes(buf_p2c[:total])
                del buf_p2c[:total]
                op1, op2 = frame[4], frame[5]
                payload  = frame[6:total-1] if total > 7 else b""
                yield ts, PHONE, op1, op2, payload
        elif handle == NOTIFY_HANDLE and att_op in ATT_NOTIFY_OPS:
            buf_c2p.extend(value)
            while len(buf_c2p) >= 6:
                if buf_c2p[0] != 0x61 or buf_c2p[1] != 0x42:
                    buf_c2p.clear(); break
                total = struct.unpack_from(">H", buf_c2p, 2)[0]
                if len(buf_c2p) < total:
                    break
                frame = bytes(buf_c2p[:total])
                del buf_c2p[:total]
                op1, op2 = frame[4], frame[5]
                payload  = frame[6:total-1] if total > 7 else b""
                yield ts, CAMERA, op1, op2, payload

def dump_file(path, label):
    events = list(iter_ios_frames(path))
    if not events:
        print(f"=== {label}: no events ===")
        return
    t0 = events[0][0]
    print(f"\n=== {label} ({len(events)} total frames) ===")
    chunk_count = 0
    for ts, d, op1, op2, payload in events:
        if op1 not in (0x82, 0x84, 0x88):
            continue
        if (op1, op2) == (0x82, 0x01):  # skip LV_FRAME
            continue
        ms = (ts - t0) * 1000
        key = (op1, op2)
        name = OP_LABELS.get(key, f"({op1:#04x},{op2:#04x})")
        if key in ((0x82, 0x21), (0x88, 0x02)):  # chunks: count only
            chunk_count += 1
            continue
        if chunk_count > 0:
            print(f"  {'':>10}       ... {chunk_count} chunk(s) ...")
            chunk_count = 0
        hex_data = payload.hex()[:48]
        print(f"  {ms:10.1f} ms  {d}  {name}  {hex_data}")
    if chunk_count > 0:
        print(f"  {'':>10}       ... {chunk_count} chunk(s) ...")

BASE_0518 = Path("f:/instax_evo_Protocol/captures/new_capture_0518/FS/data/log/bt")

for fname in ["btsnoop_hci.log.last", "btsnoop_hci.log"]:
    p = BASE_0518 / fname
    if p.exists():
        dump_file(p, f"0518/{fname}")
    else:
        print(f"  (missing: {p})")
