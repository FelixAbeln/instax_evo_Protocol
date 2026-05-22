"""
Analyze bugreport 0517b:
  - flash ON / OFF / AUTO shots with automatic image transfer
  - live view toggle on / off

Parses both btsnoop_hci.log files and annotates every IOS-Link exchange.
Run from repo root:
  python captures/new_log_0517b/analyze.py
"""
import struct
import sys
from pathlib import Path

# ── locate the two btsnoop files ─────────────────────────────────────────────
BASE = Path(__file__).parent / "FS/data/log/bt"
LOGS = [BASE / "btsnoop_hci.log", BASE / "btsnoop_hci.log.last"]

# ── btsnoop / ATT constants ───────────────────────────────────────────────────
BTSNOOP_MAGIC    = b"btsnoop\x00"
TS_OFFSET_US     = 0x00E03AB44A676000   # microseconds from btsnoop epoch to Unix epoch
ATT_WRITE_OPS    = {0x12, 0x52}         # Write Request / Write Command
ATT_NOTIFY_OPS   = {0x1B, 0x1D}         # Handle Value Notification / Indication
WRITE_HANDLE     = 0x0010               # phone → camera
NOTIFY_HANDLE    = 0x0012               # camera → phone

PHONE  = "P→C"
CAMERA = "C→P"

# ── known InfoTypes for (0x80,0x11) ──────────────────────────────────────────
INFO_TYPES = {
    0x0b: "FLASH_MODE",
    0x16: "UNKNOWN_16",
    0x17: "FILTER?",
    0x18: "UNKNOWN_18",
    0x19: "UNKNOWN_19",
    0x1a: "UNKNOWN_1a",
    0x1b: "UNKNOWN_1b",
}
FLASH_VALUES = {0x00: "AUTO", 0x01: "ON", 0x02: "OFF"}

# ── opcode labels ─────────────────────────────────────────────────────────────
OP_LABELS = {
    (0x00, 0x00): "DEVICE_PROBE",
    (0x00, 0x01): "DEVICE_INFO",
    (0x00, 0x02): "STATUS_POLL",
    (0x20, 0x10): "IMAGE_SUPPORT_INFO",
    (0x80, 0x10): "FUNC_INFO",
    (0x80, 0x11): "SET_INFO",
    (0x80, 0x15): "LIVEVIEW_INIT",
    (0x82, 0x00): "LV_START",
    (0x82, 0x01): "LV_FRAME",
    (0x82, 0x02): "LV_END",
    (0x82, 0x10): "IMG_HIST_QUERY",
    (0x82, 0x20): "IMG_HIST_POLL",
    (0x82, 0x21): "IMG_HIST_CHUNK",
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
    (0x10, 0x01): "PRINT_CMD",
    (0x10, 0x03): "PRINT_DATA",
}

# ── quiet opcodes (suppress from main timeline to reduce noise) ───────────────
QUIET = {(0x00, 0x02), (0x82, 0x01)}   # STATUS_POLL and LV_FRAME are spammy


# ─────────────────────────────────────────────────────────────────────────────
def iter_btsnoop(path: Path):
    """Yield (timestamp_s_float, direction, att_op, handle, payload_bytes)."""
    with open(path, "rb") as f:
        magic = f.read(8)
        if magic != BTSNOOP_MAGIC:
            print(f"  [WARN] {path.name}: bad magic {magic!r}", file=sys.stderr)
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

            # Only HCI ACL packets (type 0x02)
            if not data or data[0] != 0x02:
                continue
            if len(data) < 10:
                continue
            # HCI ACL header: 2B handle+flags, 2B total length
            # L2CAP header:  2B length, 2B CID
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:   # ATT
                continue

            att_op = data[9]
            if att_op not in ATT_WRITE_OPS | ATT_NOTIFY_OPS:
                continue
            if len(data) < 13:
                continue

            handle = struct.unpack_from("<H", data, 10)[0]
            value  = bytes(data[12:])

            # direction: flags bit 0: 0=controller→host(camera→phone), 1=host→controller(phone→cam)
            direction = PHONE if (flags & 1) else CAMERA
            yield ts_s, direction, att_op, handle, value


def iter_ios_frames(path: Path):
    """Reassemble IOS-Link frames from ATT fragments, yield (ts, dir, op1, op2, payload)."""
    buf_p2c = bytearray()
    buf_c2p = bytearray()

    for ts, direction, att_op, handle, value in iter_btsnoop(path):
        if handle == WRITE_HANDLE and att_op in ATT_WRITE_OPS:
            buf_p2c.extend(value)
            # Try to extract complete frames
            while len(buf_p2c) >= 6:
                if buf_p2c[0] != 0x41 or buf_p2c[1] != 0x62:
                    buf_p2c.clear()
                    break
                total = struct.unpack_from(">H", buf_p2c, 2)[0]
                if len(buf_p2c) < total:
                    break
                frame = bytes(buf_p2c[:total])
                del buf_p2c[:total]
                op1, op2 = frame[4], frame[5]
                payload  = frame[6:total - 1] if total > 7 else b""
                yield ts, PHONE, op1, op2, payload

        elif handle == NOTIFY_HANDLE and att_op in ATT_NOTIFY_OPS:
            buf_c2p.extend(value)
            while len(buf_c2p) >= 6:
                if buf_c2p[0] != 0x61 or buf_c2p[1] != 0x42:
                    buf_c2p.clear()
                    break
                total = struct.unpack_from(">H", buf_c2p, 2)[0]
                if len(buf_c2p) < total:
                    break
                frame = bytes(buf_c2p[:total])
                del buf_c2p[:total]
                op1, op2 = frame[4], frame[5]
                payload  = frame[6:total - 1] if total > 7 else b""
                yield ts, CAMERA, op1, op2, payload


def annotate(op1, op2, direction, payload: bytes) -> str:
    label = OP_LABELS.get((op1, op2), f"({op1:#04x},{op2:#04x})")

    if op1 == 0x80 and op2 == 0x11 and len(payload) >= 2:
        info_id = payload[0]
        info_name = INFO_TYPES.get(info_id, f"type_{info_id:#04x}")
        if info_id == 0x0b and len(payload) >= 3:
            val = payload[2] if len(payload) > 2 else payload[1]
            flash = FLASH_VALUES.get(val, f"0x{val:02x}")
            return f"SET_INFO  [{info_name}] = {flash}"
        return f"SET_INFO  [{info_name}] payload={payload.hex()}"

    if op1 == 0x00 and op2 == 0x01:
        # DEVICE_INFO: sub-index in payload[1]
        return f"DEVICE_INFO  {payload.hex()}"

    if op1 == 0x82 and op2 == 0x01:
        if direction == PHONE:
            return "LV_FRAME  [request]"
        jpeg_start = payload[5:7] if len(payload) >= 7 else b""
        is_jpeg = jpeg_start[:2] == b"\xff\xd8"
        size = len(payload)
        return f"LV_FRAME  [{size} B]{' JPEG' if is_jpeg else ''}"

    if op1 == 0x82 and op2 == 0x20:
        if direction == PHONE:
            return "IMG_HIST_POLL  [check ready]"
        if len(payload) >= 1 and payload[0] == 0x02 and len(payload) == 1:
            return "IMG_HIST_POLL  not ready"
        if len(payload) >= 9:
            total_bytes = struct.unpack_from(">I", payload, 2)[0]
            chunks      = struct.unpack_from(">I", payload, 6)[0]
            return f"IMG_HIST_POLL  READY  total={total_bytes} B  chunks={chunks}"
        return f"IMG_HIST_POLL  {payload.hex()}"

    if op1 == 0x82 and op2 == 0x21:
        if direction == PHONE:
            chunk_idx = struct.unpack_from(">I", payload, 0)[0] if len(payload) >= 4 else -1
            return f"IMG_HIST_CHUNK  ACK chunk#{chunk_idx}"
        chunk_idx = struct.unpack_from(">I", payload, 0)[0] if len(payload) >= 4 else -1
        data = payload[4:] if len(payload) > 4 else b""
        is_jpeg = data[:2] == b"\xff\xd8"
        return f"IMG_HIST_CHUNK  #{chunk_idx}  {len(data)} B{' (JPEG start)' if is_jpeg else ''}"

    if op1 == 0x88:
        sub = {0x00:"QUERY", 0x01:"READY", 0x02:"CHUNK", 0x03:"ACK", 0x04:"DONE"}.get(op2, f"0x{op2:02x}")
        extra = f"  {payload.hex()[:32]}" if payload else ""
        return f"PULL_{sub}{extra}"

    if op1 == 0x00 and op2 == 0x02:
        return f"STATUS_POLL  {payload.hex()}"

    return f"{label}  {payload.hex()[:48]}"


# ─────────────────────────────────────────────────────────────────────────────
def main():
    for log_path in LOGS:
        if not log_path.exists():
            continue

        print(f"\n{'='*70}")
        print(f"  {log_path.name}  ({log_path.stat().st_size // 1024} KB)")
        print(f"{'='*70}\n")

        frames = list(iter_ios_frames(log_path))
        if not frames:
            print("  No IOS-Link frames found.")
            continue

        t0  = frames[0][0]
        total = len(frames)

        # ── statistics ────────────────────────────────────────────────────────
        lv_frames   = sum(1 for _, d, o1, o2, _ in frames if o1 == 0x82 and o2 == 0x01 and d == CAMERA)
        set_info    = [(ts, d, p) for ts, d, o1, o2, p in frames if o1 == 0x80 and o2 == 0x11]
        hist_chunks = [(ts, d, p) for ts, d, o1, o2, p in frames if o1 == 0x82 and o2 == 0x21 and d == CAMERA]
        pull_chunks = [(ts, d, p) for ts, d, o1, o2, p in frames if o1 == 0x88 and o2 == 0x02 and d == CAMERA]

        print(f"Total IOS-Link frames : {total}")
        print(f"Live view frames (cam): {lv_frames}")
        print(f"SET_INFO exchanges    : {len(set_info)}")
        print(f"IMG_HIST chunks recv  : {len(hist_chunks)}")
        print(f"PULL (0x88) chunks    : {len(pull_chunks)}")
        print()

        # ── full timeline (suppress spammy opcodes) ───────────────────────────
        print("─── Timeline (STATUS_POLL + LV_FRAME suppressed) ───────────────\n")

        lv_session = None
        lv_count   = 0
        img_session_chunks = 0
        img_session_bytes  = 0
        img_session_start  = None

        for ts, direction, op1, op2, payload in frames:
            rel_ms = (ts - t0) * 1000

            # Track live view sessions silently
            if op1 == 0x82 and op2 == 0x00 and direction == PHONE:
                lv_session = ts
                lv_count   = 0
            if op1 == 0x82 and op2 == 0x01 and direction == CAMERA:
                lv_count += 1
            if op1 == 0x82 and op2 == 0x02:
                if lv_session is not None:
                    dur = ts - lv_session
                    fps = lv_count / dur if dur > 0 else 0
                    print(f"{rel_ms:10.1f} ms  {direction}  LV_END  "
                          f"[{lv_count} frames  {dur:.1f} s  {fps:.1f} fps]")
                    lv_session = None
                    lv_count   = 0
                    continue

            # Track IMG_HIST transfers silently (one line per transfer)
            if op1 == 0x82 and op2 == 0x20 and direction == CAMERA and len(payload) > 1:
                img_session_start  = ts
                img_session_chunks = 0
                img_session_bytes  = 0
            if op1 == 0x82 and op2 == 0x21 and direction == CAMERA:
                img_session_chunks += 1
                img_session_bytes  += max(0, len(payload) - 4)
                # last chunk: no following ACK from phone (phone ACKs each chunk)
                # detect end by watching for next non-chunk event — handled below

            # Suppress high-frequency opcodes
            if (op1, op2) in QUIET:
                continue

            # Print IMG_HIST transfer summary when POLL sends "ready" response
            if op1 == 0x82 and op2 == 0x20 and direction == CAMERA and len(payload) > 1:
                note = annotate(op1, op2, direction, payload)
                print(f"{rel_ms:10.1f} ms  {direction}  {note}")
                continue

            if op1 == 0x82 and op2 == 0x21:
                # Show only first and last chunk
                if img_session_chunks == 1:
                    print(f"{rel_ms:10.1f} ms  {direction}  "
                          f"IMG_HIST_CHUNK  first  {len(payload)-4} B")
                continue

            print(f"{rel_ms:10.1f} ms  {direction}  {annotate(op1, op2, direction, payload)}")

        # ── Summary of IMG_HIST transfer if still open ────────────────────────
        if img_session_chunks > 0:
            dur = (frames[-1][0] - img_session_start) if img_session_start else 0
            print(f"\n  [IMG_HIST transfer total: {img_session_chunks} chunks, "
                  f"{img_session_bytes} B, ~{dur:.1f} s]")

        # ── Flash changes ──────────────────────────────────────────────────────
        print("\n─── Flash / SET_INFO changes ────────────────────────────────────\n")
        for ts, direction, payload in set_info:
            rel_ms = (ts - t0) * 1000
            if len(payload) >= 1:
                info_id   = payload[0]
                info_name = INFO_TYPES.get(info_id, f"type_{info_id:#04x}")
                if info_id == 0x0b and len(payload) >= 3:
                    val   = payload[2]
                    flash = FLASH_VALUES.get(val, f"0x{val:02x}")
                    print(f"  {rel_ms:10.1f} ms  {direction}  FLASH = {flash:<4}  raw={payload.hex()}")
                else:
                    print(f"  {rel_ms:10.1f} ms  {direction}  {info_name} = {payload.hex()}")

        # ── IMG_HIST chunk sizes ───────────────────────────────────────────────
        if hist_chunks:
            print("\n─── IMG_HIST chunk summary ──────────────────────────────────────\n")
            sizes = [max(0, len(p) - 4) for _, _, p in hist_chunks]
            total_bytes = sum(sizes)
            # Detect JPEG signature in first chunk
            first_data = hist_chunks[0][2][4:] if hist_chunks else b""
            print(f"  Chunks : {len(hist_chunks)}")
            print(f"  Total  : {total_bytes:,} B  ({total_bytes/1024:.1f} KB)")
            print(f"  First 4 bytes of chunk 0 data: {first_data[:8].hex()}")
            if first_data[:2] == b"\xff\xd8":
                print("  → JPEG confirmed")

        # ── 0x88 pull summary ─────────────────────────────────────────────────
        if pull_chunks:
            sizes = [max(0, len(p)) for _, _, p in pull_chunks]
            print(f"\n─── 0x88 PULL chunks: {len(pull_chunks)}  "
                  f"total {sum(sizes):,} B ─────────────────────\n")

        print()


if __name__ == "__main__":
    main()
