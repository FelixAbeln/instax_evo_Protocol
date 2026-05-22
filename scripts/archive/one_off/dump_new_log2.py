"""Deep dive: new opcodes, register reads, and shot region in 2139 log."""
import struct

LOG = r'c:\Users\Compf\Downloads\Phone Link\br_2139\FS\data\log\bt\btsnoop_hci.log'
BTSNOOP_MAGIC = b'btsnoop\x00'


def iter_packets(path):
    with open(path, 'rb') as f:
        assert f.read(8) == BTSNOOP_MAGIC
        f.read(8)
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig_len, inc_len, flags, drops, ts = struct.unpack('>IIIIQ', hdr)
            data = f.read(inc_len)
            if len(data) < inc_len:
                break
            yield flags, ts, data


def get_att_value(pkt):
    if len(pkt) < 2 or pkt[0] != 0x02:
        return None
    acl = pkt[1:]
    if len(acl) < 9:
        return None
    cid = struct.unpack_from('<H', acl, 6)[0]
    if cid != 0x0004:
        return None
    att = acl[8:]
    if len(att) < 3:
        return None
    return att[0], att[1:3], att[3:]


WRITE_OPS  = {0x12, 0x52}
NOTIFY_OPS = {0x1B, 0x1D}
bufs = {0: bytearray(), 1: bytearray()}
DIR_SYNC = {0: (0x41, 0x62), 1: (0x61, 0x42)}
frames = []
global_ts0 = None

for flags, ts, pkt in iter_packets(LOG):
    if global_ts0 is None:
        global_ts0 = ts
    result = get_att_value(pkt)
    if result is None:
        continue
    att_op, _, att_val = result
    direction = 0 if att_op in WRITE_OPS else (1 if att_op in NOTIFY_OPS else -1)
    if direction < 0 or len(att_val) < 2:
        continue
    s1, s2 = DIR_SYNC[direction]
    buf = bufs[direction]
    if att_val[0] == s1 and att_val[1] == s2:
        bufs[direction] = bytearray(att_val)
        buf = bufs[direction]
    elif len(buf) >= 4 and buf[0] == s1 and buf[1] == s2:
        buf.extend(att_val)
    else:
        bufs[direction] = bytearray()
        continue
    if len(buf) < 6:
        continue
    total = struct.unpack_from('>H', buf, 2)[0]
    if len(buf) < total:
        continue
    frame = bytes(buf[:total])
    bufs[direction] = bytearray()
    op1, op2 = frame[4], frame[5]
    payload = frame[6:total - 1]
    rel_ms = (ts - global_ts0) // 1000
    frames.append((ts, rel_ms, direction, op1, op2, payload))

# --- 1. New opcodes (20,10) and (80,10) ---
print("=== (20,10) and (80,10) frames ===")
for ts, rel_ms, direction, op1, op2, payload in frames:
    if (op1, op2) in ((0x20, 0x10), (0x80, 0x10)):
        tag = 'P->C' if direction == 0 else 'C->P'
        h = ' '.join(f'{b:02x}' for b in payload)
        print(f"  +{rel_ms:7d}ms  ({op1:02x},{op2:02x}) {tag} [{len(payload)}B]  {h}")

# --- 2. (80,11) register reads ---
print("\n=== (80,11) register read/write frames ===")
for ts, rel_ms, direction, op1, op2, payload in frames:
    if op1 == 0x80 and op2 == 0x11:
        tag = 'P->C' if direction == 0 else 'C->P'
        h = ' '.join(f'{b:02x}' for b in payload)
        print(f"  +{rel_ms:7d}ms  ({op1:02x},{op2:02x}) {tag} [{len(payload)}B]  {h}")

# --- 3. Full sequence around shots (±5s) ---
shot_times = [843273, 855723, 867513]
print("\n=== Full frame sequence around shots (±5000ms window) ===")
for shot_t in shot_times:
    print(f"\n  --- shot region near +{shot_t}ms ---")
    for ts, rel_ms, direction, op1, op2, payload in frames:
        if abs(rel_ms - shot_t) <= 5000:
            tag = 'P->C' if direction == 0 else 'C->P'
            h = ' '.join(f'{b:02x}' for b in payload[:40])
            suffix = '...' if len(payload) > 40 else ''
            print(f"  +{rel_ms:7d}ms  ({op1:02x},{op2:02x}) {tag} [{len(payload)}B]  {h}{suffix}")

# --- 4. 84,00 C->P decoded ---
print("\n=== 84,00 C->P responses decoded ===")
for ts, rel_ms, direction, op1, op2, payload in frames:
    if op1 == 0x84 and op2 == 0x00 and direction == 1:
        h = ' '.join(f'{b:02x}' for b in payload)
        if len(payload) >= 12:
            a = struct.unpack_from('<I', payload, 4)[0]
            b2 = struct.unpack_from('<I', payload, 8)[0]
            print(f"  +{rel_ms:7d}ms  84,00 C->P [{len(payload)}B] {h}  val1={a} val2={b2}")
        else:
            print(f"  +{rel_ms:7d}ms  84,00 C->P [{len(payload)}B] {h}")
