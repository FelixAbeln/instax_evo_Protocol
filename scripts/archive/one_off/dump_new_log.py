"""Quick dump of the 2139 bugreport btsnoop — opcodes, counter bytes, HIST frames."""
import struct
import collections

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

print(f"Total frames decoded: {len(frames)}\n")

# 1. All unique opcodes
seen = collections.Counter()
for ts, rel_ms, direction, op1, op2, payload in frames:
    tag = 'P→C' if direction == 0 else 'C→P'
    seen[(op1, op2, tag)] += 1

print("=== All unique opcodes ===")
for (op1, op2, tag), cnt in sorted(seen.items()):
    print(f"  ({op1:02x},{op2:02x}) {tag}  x{cnt}")

# 2. All (00,02) C→P responses — only print when value changes
print("\n=== (00,02) C→P responses (changed values only) ===")
last = {}
for ts, rel_ms, direction, op1, op2, payload in frames:
    if op1 != 0x00 or op2 != 0x02 or direction != 1:
        continue
    if len(payload) < 2:
        continue
    sub = payload[1]
    h = ' '.join(f'{b:02x}' for b in payload)
    if last.get(sub) != payload:
        mark = '  *** CHANGED ***' if sub in last else '  (first)'
        last[sub] = payload
        print(f"  +{rel_ms:7d}ms  sub={sub:02x}  [{len(payload)}B]  {h}{mark}")

# 3. All 84,xx frames (HIST)
print("\n=== All (84,xx) HIST frames ===")
for ts, rel_ms, direction, op1, op2, payload in frames:
    if op1 != 0x84:
        continue
    tag = 'P→C' if direction == 0 else 'C→P'
    h = ' '.join(f'{b:02x}' for b in payload[:60])
    suffix = '...' if len(payload) > 60 else ''
    print(f"  +{rel_ms:7d}ms  ({op1:02x},{op2:02x}) {tag} [{len(payload)}B]  {h}{suffix}")

# 4. New opcodes
OLD = {
    (0x00, 0x00), (0x00, 0x01), (0x00, 0x02),
    (0x80, 0x00), (0x80, 0x01), (0x80, 0x11), (0x80, 0x15),
    (0x82, 0x00), (0x82, 0x01), (0x82, 0x02),
    (0x82, 0x10), (0x82, 0x20), (0x82, 0x21), (0x82, 0x22),
    (0x84, 0x00), (0x84, 0x01), (0x84, 0x02),
    (0x84, 0x09), (0x84, 0x0a), (0x84, 0x0b),
    (0x88, 0x00), (0x88, 0x01), (0x88, 0x02), (0x88, 0x03),
    (0x88, 0x04), (0x88, 0x05), (0x88, 0x06), (0x88, 0x07),
    (0x88, 0x08), (0x88, 0x09), (0x88, 0x0a), (0x88, 0x0b),
}
print("\n=== NEW opcodes not seen in previous logs ===")
found_new = False
for (op1, op2, tag), cnt in sorted(seen.items()):
    if (op1, op2) not in OLD:
        print(f"  ({op1:02x},{op2:02x}) {tag}  x{cnt}  *** NEW ***")
        found_new = True
if not found_new:
    print("  (none)")
