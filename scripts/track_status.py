"""
Track (00,02) polling responses over time.
Only prints a response when it CHANGES from the previous value for that subcommand.
This shows exactly when shot/effect state changes during the session.
"""
import struct

LOG = r'c:\Users\Compf\Downloads\Phone Link\bugreport_0518b\FS\data\log\bt\btsnoop_hci.log'
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
    att_op, att_handle_bytes, att_val = result

    direction = -1
    if att_op in WRITE_OPS:
        direction = 0
    elif att_op in NOTIFY_OPS:
        direction = 1
    else:
        continue

    if len(att_val) < 2:
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


# Track (00,02) C→P response changes per subcommand
print("=== (00,02) Camera→Phone response changes over time ===\n")
last_by_sub = {}  # sub_byte → last payload bytes

for ts, rel_ms, direction, op1, op2, payload in frames:
    if op1 != 0x00 or op2 != 0x02:
        continue
    if direction != 1:  # C→P only
        continue
    if len(payload) < 2:
        continue

    sub = payload[1]  # response echoes the subcommand byte at position 1
    pay_hex = ' '.join(f'{b:02x}' for b in payload)

    if last_by_sub.get(sub) != payload:
        changed = '  *** CHANGED ***' if sub in last_by_sub else '  (first seen)'
        print(f"  +{rel_ms:7d}ms  sub={sub:02x}  [{len(payload)}B]  {pay_hex}{changed}")
        last_by_sub[sub] = payload

print()
print("=== All non-(00,02) C→P frames (camera-initiated or infrequent) ===\n")
for ts, rel_ms, direction, op1, op2, payload in frames:
    if direction != 1:
        continue
    if op1 == 0x00 and op2 == 0x02:
        continue
    pay_hex = ' '.join(f'{b:02x}' for b in payload)
    print(f"  +{rel_ms:7d}ms  C→P  ({op1:02x},{op2:02x})  [{len(payload)}B]  {pay_hex}")
