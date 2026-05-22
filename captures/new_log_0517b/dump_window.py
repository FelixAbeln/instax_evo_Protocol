"""Dump all IOS-Link frames in a time window, including STATUS_POLL responses."""
import struct, sys
from pathlib import Path

BASE = Path("captures/new_log_0517b/FS/data/log/bt")
BTSNOOP_MAGIC = b"btsnoop\x00"
TS_OFFSET_US  = 0x00E03AB44A676000
ATT_WRITE_OPS  = {0x12, 0x52}
ATT_NOTIFY_OPS = {0x1B, 0x1D}
WRITE_HANDLE   = 0x0010
NOTIFY_HANDLE  = 0x0012
IOS_HDR_P = b"\x41\x62"
IOS_HDR_C = b"\x61\x42"

WIN_START_MS = float(sys.argv[1]) if len(sys.argv) > 1 else 15000
WIN_END_MS   = float(sys.argv[2]) if len(sys.argv) > 2 else 22000

SUPPORT_FUNCTION_INFO = (0x00, 0x02)
INFO_TYPE_CAMERA_FUNCTION = 0x04

def iter_btsnoop(path):
    with open(path, "rb") as f:
        if f.read(8) != BTSNOOP_MAGIC:
            return
        f.read(8)
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", hdr[:16])
            ts_us = struct.unpack(">q", hdr[16:])[0]
            ts_s  = (ts_us - TS_OFFSET_US) / 1_000_000
            data  = f.read(inc_len)
            if not data or data[0] != 0x02 or len(data) < 13:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:  # ATT only
                continue
            att_op = data[9]
            handle = struct.unpack_from("<H", data, 10)[0]
            value  = bytes(data[12:])
            if att_op in ATT_WRITE_OPS and handle == WRITE_HANDLE:
                yield ts_s, "P->C", value
            elif att_op in ATT_NOTIFY_OPS and handle == NOTIFY_HANDLE:
                yield ts_s, "C->P", value

buf_p, buf_c = bytearray(), bytearray()
t0 = None

for path in [BASE / "btsnoop_hci.log"]:
    for ts, direction, payload in iter_btsnoop(path):
        if t0 is None:
            t0 = ts
        ms = (ts - t0) * 1000
        # Feed ALL packets into the reassembly buffers regardless of window,
        # so multi-ATT-fragment frames that straddle the window boundary are
        # still reassembled correctly.
        buf = buf_p if direction == "P->C" else buf_c
        buf.extend(payload)
        hdr = IOS_HDR_P if direction == "P->C" else IOS_HDR_C
        while len(buf) >= 6:
            if buf[0:2] != hdr:
                buf.clear()
                break
            total = struct.unpack_from(">H", buf, 2)[0]
            if len(buf) < total:
                break
            pkt = bytes(buf[:total])
            buf[:total] = b""
            op1, op2 = pkt[4], pkt[5]
            pl = pkt[6:-1]

            # Only print frames whose timestamp is inside the window
            if ms < WIN_START_MS or ms > WIN_END_MS:
                continue

            # Annotate CAMERA_FUNCTION_INFO responses
            note = ""
            if op1 == 0x00 and op2 == 0x02:
                if direction == "P->C" and len(pl) >= 1:
                    itype = pl[0]
                    note = f"  [poll InfoType={itype:#04x}]"
                elif direction == "C->P" and len(pl) >= 5:
                    itype = pl[1] if len(pl) > 1 else -1
                    if itype == INFO_TYPE_CAMERA_FUNCTION:
                        note = f"  *** CAMERA_FUNCTION_INFO data[2]={pl[4]:#04x} data={pl[2:].hex()} ***"
                    else:
                        note = f"  [resp InfoType={itype:#04x}]"
            print(f"{ms:10.1f} ms  {direction}  ({op1:#04x},{op2:#04x})  {pl.hex()}{note}")
