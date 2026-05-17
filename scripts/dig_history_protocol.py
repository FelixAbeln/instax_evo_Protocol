"""
Deep dig into all btsnoop captures to find the actual stored print-history protocol.

We know:
  - 0x82 group = live view (NOT stored prints)
  - Stored print history retrieval mechanism = UNKNOWN

This script scans EVERY btsnoop file across all captures and:
  1. Lists all unique IOS Link op-code pairs seen
  2. Shows every camera→phone (notify) IOS Link response
  3. Identifies sessions with large cam→phone data bursts (potential image transfers)
  4. Displays full opcode timeline per session, collapsing repeats

Run from workspace root:
  python scripts/dig_history_protocol.py [--verbose] [--file <path>]
"""
import struct
import sys
from pathlib import Path
from collections import defaultdict

# ── CLI flags ────────────────────────────────────────────────────────────────
VERBOSE  = "--verbose" in sys.argv
SPECIFIC = None
if "--file" in sys.argv:
    idx = sys.argv.index("--file")
    if idx + 1 < len(sys.argv):
        SPECIFIC = Path(sys.argv[idx + 1])

# ── IOS Link characteristic handles (per-camera) ─────────────────────────────
# Mini Evo: write=0x0014, notify=0x0016
# Wide Evo: write=0x0010, notify=0x0012
WRITE_HANDLES  = {0x0010, 0x0014}
NOTIFY_HANDLES = {0x0012, 0x0016}
# Android profile (Evo Wide second BLE profile)
ANDROID_WRITE  = {0x0020, 0x001D}
ANDROID_NOTIFY = {0x001D, 0x0020}

ATT_WRITE_OPS  = {0x12, 0x52}   # Write Request, Write Command
ATT_NOTIFY_OPS = {0x1B, 0x1D}   # Handle Value Notification, Indication

# ── btsnoop helpers ───────────────────────────────────────────────────────────

def parse_btsnoop(path: Path):
    """
    Yield (ts_us_rel, direction, att_op, handle, value) for every ATT L2CAP packet.
    direction: 'host→dev' (flags bit0=0) or 'dev→host' (flags bit0=1)
    """
    try:
        data = path.read_bytes()
    except OSError as e:
        print(f"  [SKIP] Cannot read {path}: {e}")
        return

    # btsnoop file header: magic (8 bytes) + version (4) + datalink (4) = 16 bytes
    if data[:8] != b'btsnoop\x00':
        print(f"  [SKIP] Not a btsnoop file: {path.name}")
        return

    t0 = None
    pos = 16
    while pos + 24 <= len(data):
        orig_len = struct.unpack_from(">I", data, pos)[0]
        inc_len  = struct.unpack_from(">I", data, pos + 4)[0]
        flags    = struct.unpack_from(">I", data, pos + 8)[0]
        # drops  = struct.unpack_from(">I", data, pos + 12)[0]
        ts_us    = struct.unpack_from(">q", data, pos + 16)[0]
        pos += 24

        pkt = data[pos:pos + inc_len]
        pos += inc_len

        if t0 is None:
            t0 = ts_us
        rel_us = ts_us - t0

        # Only HCI ACL data packets (indicator byte 0x02)
        if not pkt or pkt[0] != 0x02 or len(pkt) < 12:
            continue

        # L2CAP CID must be 0x0004 (ATT)
        cid = struct.unpack_from("<H", pkt, 7)[0]
        if cid != 0x0004:
            continue

        att_op = pkt[9]
        if len(pkt) < 12:
            continue
        handle = struct.unpack_from("<H", pkt, 10)[0]
        value  = pkt[12:]

        direction = "dev→host" if (flags & 1) else "host→dev"
        yield rel_us, direction, att_op, handle, value


def decode_ios_link(raw: bytes):
    """
    Parse IOS Link framing: 41 62 [total_len BE2] [op1] [op2] [payload…] [xor]
    Returns (op1, op2, payload) or None.
    """
    if len(raw) < 7 or raw[0] != 0x41 or raw[1] != 0x62:
        return None
    total_len = struct.unpack_from(">H", raw, 2)[0]
    if len(raw) < total_len or total_len < 7:
        return None
    op1     = raw[4]
    op2     = raw[5]
    payload = raw[6:total_len - 1]
    return op1, op2, payload


def decode_cam_response(raw: bytes):
    """
    Parse camera→phone IOS Link framing: 61 42 [total_len BE2] [op1] [op2] [payload…] [xor]
    """
    if len(raw) < 7 or raw[0] != 0x61 or raw[1] != 0x42:
        return None
    total_len = struct.unpack_from(">H", raw, 2)[0]
    if len(raw) < total_len or total_len < 7:
        return None
    op1     = raw[4]
    op2     = raw[5]
    payload = raw[6:total_len - 1]
    return op1, op2, payload


# ── Locate all btsnoop files ──────────────────────────────────────────────────

BASE = Path("captures/extracted")
if not BASE.exists():
    BASE = Path("f:/instax_evo_Protocol/captures/extracted")

if SPECIFIC:
    log_files = [SPECIFIC]
else:
    log_files = sorted(BASE.rglob("btsnoop_hci*"))
    # exclude non-binary .log files (some are .last.log = renamed text)
    log_files = [p for p in log_files if not p.suffix == ".gz"]

print(f"Found {len(log_files)} btsnoop file(s):")
for f in log_files:
    size_kb = f.stat().st_size // 1024
    print(f"  {size_kb:6d} KB  {f.relative_to(BASE) if BASE in f.parents else f}")
print()

# ── Global opcode inventory ────────────────────────────────────────────────────

all_phone_ops   = defaultdict(int)   # (op1,op2) -> count
all_cam_ops     = defaultdict(int)
all_cam_bursts  = []                 # (file, session, t_s, total_bytes)

# ── Per-file analysis ─────────────────────────────────────────────────────────

for log_path in log_files:
    size_kb = log_path.stat().st_size // 1024
    print("=" * 72)
    print(f"FILE: {log_path.name}  ({size_kb} KB)")
    print("=" * 72)

    # Track sessions by gap (>5 s gap = new BLE session)
    SESSION_GAP_US = 5_000_000  # 5 s

    sessions = []       # list of session dicts
    cur_sess = None

    def new_session(t_us):
        return {
            "start_us": t_us,
            "phone_ops": [],   # (t_s, op1, op2, payload)
            "cam_ops":   [],   # (t_s, op1, op2, payload)
            "cam_raw":   [],   # raw bytes for non-framed cam notifications
            "android_writes": [],
        }

    for rel_us, direction, att_op, handle, value in parse_btsnoop(log_path):
        # Session boundary detection
        if cur_sess is None or (rel_us - cur_sess["start_us"]) > SESSION_GAP_US:
            if cur_sess:
                sessions.append(cur_sess)
            cur_sess = new_session(rel_us)

        t_s = rel_us / 1_000_000

        # IOS Link phone→camera (writes on write handle)
        if handle in WRITE_HANDLES and att_op in ATT_WRITE_OPS:
            dec = decode_ios_link(value)
            if dec:
                op1, op2, payload = dec
                cur_sess["phone_ops"].append((t_s, op1, op2, payload))
                all_phone_ops[(op1, op2)] += 1

        # IOS Link camera→phone (notifications on notify handle)
        elif handle in NOTIFY_HANDLES and att_op in ATT_NOTIFY_OPS:
            dec = decode_cam_response(value)
            if dec:
                op1, op2, payload = dec
                cur_sess["cam_ops"].append((t_s, op1, op2, payload))
                all_cam_ops[(op1, op2)] += 1
            else:
                # Raw (continuation fragment — no framing header)
                cur_sess["cam_raw"].append((t_s, bytes(value)))

        # Android profile writes
        elif handle in ANDROID_WRITE and att_op in ATT_WRITE_OPS:
            cur_sess["android_writes"].append((t_s, bytes(value)))

    if cur_sess:
        sessions.append(cur_sess)

    print(f"  {len(sessions)} session(s) detected\n")

    for si, sess in enumerate(sessions):
        phone_ops = sess["phone_ops"]
        cam_ops   = sess["cam_ops"]
        cam_raw   = sess["cam_raw"]
        android   = sess["android_writes"]

        total_cam_bytes = sum(len(v) for _, v in cam_raw) + \
                          sum(len(p) + 6 for _, _, _, p in cam_ops)

        print(f"  ── Session {si+1}  "
              f"phone_ops={len(phone_ops)}  cam_resp={len(cam_ops)}  "
              f"cam_raw_frags={len(cam_raw)}  android_writes={len(android)}  "
              f"~cam_bytes={total_cam_bytes}")

        if total_cam_bytes > 500:
            all_cam_bursts.append({
                "file": log_path.name, "session": si+1,
                "total_bytes": total_cam_bytes,
                "cam_ops_count": len(cam_ops),
                "raw_frags": len(cam_raw),
            })

        # ── Print full timeline (phone→cam ops, collapsing repeats) ─────────
        print(f"    Phone→cam ops timeline:")
        prev_key = None
        run_count = 0
        run_start = None
        for t_s, op1, op2, payload in phone_ops:
            key = (op1, op2)
            if key == prev_key:
                run_count += 1
            else:
                if run_count > 1:
                    print(f"      ... ×{run_count} (last t={t_s:.2f}s)")
                prev_key = key
                run_count = 1
                run_start = t_s
                pstr = payload[:16].hex() if payload else ""
                # Mark interesting non-data ops
                flag = ""
                if op1 not in (0x10, 0x82) and not (op1 == 0x10 and op2 == 0x01):
                    flag = "  ◄"
                print(f"      t={t_s:8.2f}s  ({op1:#04x},{op2:#04x})  [{len(payload)}B] {pstr}{flag}")
        if run_count > 1:
            print(f"      ... ×{run_count} total")

        # ── All cam→phone IOS Link responses ────────────────────────────────
        if cam_ops:
            print(f"    Cam→phone IOS Link responses ({len(cam_ops)}):")
            prev_key = None
            run_count = 0
            for t_s, op1, op2, payload in cam_ops:
                key = (op1, op2)
                if key == prev_key:
                    run_count += 1
                else:
                    if run_count > 1:
                        print(f"      ... ×{run_count}")
                    prev_key = key
                    run_count = 1
                    pstr = payload[:20].hex() if payload else "(empty)"
                    print(f"      t={t_s:8.2f}s  ({op1:#04x},{op2:#04x})  [{len(payload)}B] {pstr}")
            if run_count > 1:
                print(f"      ... ×{run_count}")

        # ── Raw cam fragments summary ────────────────────────────────────────
        if cam_raw:
            total_raw = sum(len(v) for _, v in cam_raw)
            first_bytes = cam_raw[0][1][:8].hex() if cam_raw else ""
            last_bytes  = cam_raw[-1][1][:8].hex() if cam_raw else ""
            print(f"    Raw cam frags: {len(cam_raw)}×  total={total_raw}B  "
                  f"first={first_bytes}  last={last_bytes}")
            if VERBOSE and cam_raw:
                for t_s, v in cam_raw[:5]:
                    print(f"      t={t_s:.2f}s  [{len(v)}B]  {v[:24].hex()}")

        # ── Android writes summary ───────────────────────────────────────────
        if android:
            non_img = [(t, v) for t, v in android if not (0x90 <= v[0] <= 0xEF)]
            if non_img:
                print(f"    Android CMD writes ({len(non_img)} non-image):")
                for t_s, v in non_img[:10]:
                    print(f"      t={t_s:8.2f}s  {v[:24].hex()}")
            else:
                img_bytes = sum(len(v) for _, v in android)
                print(f"    Android IMG writes: {len(android)}×  total={img_bytes}B")

        print()

# ── Global opcode summary ──────────────────────────────────────────────────────

print()
print("=" * 72)
print("GLOBAL OPCODE INVENTORY (phone→camera IOS Link)")
print("=" * 72)
print(f"  {'(op1,op2)':<18} {'count':>6}  description")
# Known opcode names
KNOWN = {
    (0x00,0x00): "SUPPORT_FUNCTION_AND_VERSION_INFO",
    (0x00,0x01): "DEVICE_INFO_SERVICE",
    (0x00,0x02): "SUPPORT_FUNCTION_INFO",
    (0x10,0x00): "PRINT_IMAGE_DOWNLOAD_START",
    (0x10,0x01): "PRINT_IMAGE_DOWNLOAD_DATA",
    (0x10,0x02): "PRINT_IMAGE_DOWNLOAD_END",
    (0x10,0x80): "PRINT_IMAGE",
    (0x10,0x81): "REJECT_FILM_COVER",
    (0x20,0x00): "FW_DOWNLOAD_START",
    (0x20,0x10): "FW_PROGRAM_INFO",
    (0x30,0x00): "XYZ_AXIS_INFO",
    (0x30,0x01): "LED_PATTERN_SETTINGS",
    (0x80,0x00): "CAMERA_SETTINGS",
    (0x80,0x01): "CAMERA_SETTINGS_GET",
    (0x80,0x10): "CONFIG_REGISTER_BANK",
    (0x80,0x11): "CONFIG_REGISTER_READ",
    (0x80,0x15): "LIVE_VIEW_PREPARE",
    (0x82,0x00): "LIVE_VIEW_START",
    (0x82,0x01): "LIVE_VIEW_FRAME (pull)",
    (0x82,0x02): "LIVE_VIEW_END",
    (0x84,0x00): "CAMERA_LOG_SUBTOTAL_START",
    (0x84,0x01): "CAMERA_LOG_SUBTOTAL_DATA",
    (0x84,0x02): "CAMERA_LOG_SUBTOTAL_CLEAR",
    (0x84,0x03): "CAMERA_LOG_DATE_START",
    (0x84,0x06): "CAMERA_LOG_FILTER_START",
    (0x84,0x09): "LIVE_VIEW_SLOT_QUERY",
    (0x84,0x0a): "LIVE_VIEW_SLOT_SUB_QUERY",
    (0x84,0x0b): "LIVE_VIEW_SLOT_ACK",
}
for key in sorted(all_phone_ops):
    cnt  = all_phone_ops[key]
    name = KNOWN.get(key, "*** UNKNOWN ***")
    star = " ★" if key not in KNOWN else ""
    print(f"  ({key[0]:#04x},{key[1]:#04x})         {cnt:6d}  {name}{star}")

print()
print("=" * 72)
print("GLOBAL OPCODE INVENTORY (camera→phone IOS Link responses)")
print("=" * 72)
for key in sorted(all_cam_ops):
    cnt  = all_cam_ops[key]
    name = KNOWN.get(key, "*** UNKNOWN ***")
    star = " ★" if key not in KNOWN else ""
    print(f"  ({key[0]:#04x},{key[1]:#04x})         {cnt:6d}  {name}{star}")

print()
print("=" * 72)
print("LARGE CAM→PHONE DATA BURSTS  (potential image transfers)")
print("=" * 72)
all_cam_bursts.sort(key=lambda x: -x["total_bytes"])
for b in all_cam_bursts:
    print(f"  {b['file']}  sess={b['session']}  "
          f"~{b['total_bytes']//1024} KB  "
          f"({b['cam_ops_count']} framed + {b['raw_frags']} raw frags)")
