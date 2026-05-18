"""
Dump ALL IOS-Link frames from the btsnoop in chronological order.
Shows both P→C (41 62) and C→P (61 42) frames.
This reveals the full phone↔camera conversation, including any
live shot/effect notifications that don't come from HIST.
"""
import struct

LOG = r'c:\Users\Compf\Downloads\Phone Link\bugreport_0518b\FS\data\log\bt\btsnoop_hci.log'

BTSNOOP_MAGIC = b'btsnoop\x00'

BASELINE_TS = None  # first packet timestamp for relative time display


def iter_packets(path):
    with open(path, 'rb') as f:
        assert f.read(8) == BTSNOOP_MAGIC
        f.read(8)  # version + datalink
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
    """Return (direction, att_opcode, att_handle_le, att_value) or None."""
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
    att_op = att[0]
    return att_op, att[1:3], att[3:]


# Reassembly buffers: one for each direction
bufs = {0: bytearray(), 1: bytearray()}  # 0=P→C (phone sends), 1=C→P (camera sends)
DIR_SYNC = {0: (0x41, 0x62), 1: (0x61, 0x42)}  # sync bytes per direction
DIR_NAME = {0: 'P→C', 1: 'C→P'}

# ATT opcodes that carry data in our direction
WRITE_OPS = {0x12, 0x52}   # ATT Write Request / Write Command (P→C)
NOTIFY_OPS = {0x1B, 0x1D}  # ATT Handle Value Notification/Indication (C→P)

frames = []  # (ts, direction, op1, op2, payload_bytes)

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
        direction = 0  # phone → camera
    elif att_op in NOTIFY_OPS:
        direction = 1  # camera → phone
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
    rel_ms = (ts - global_ts0) // 1000  # microseconds to ms

    frames.append((ts, rel_ms, direction, op1, op2, payload))


print(f"Total IOS-Link frames decoded: {len(frames)}")
print()

# Group by op to find unique opcodes
from collections import Counter
op_counts = Counter((d, f'{o1:02x},{o2:02x}') for _, _, d, o1, o2, _ in frames)
print("Opcode summary (direction, op1, op2 → count):")
for (d, op), cnt in sorted(op_counts.items(), key=lambda x: -x[1]):
    print(f"  {DIR_NAME[d]:3s}  ({op})  × {cnt}")
print()

# Print all frames NOT matching the most common opcodes (to find unusual/live ones)
FILTER_OUT = {(0, '50,11'), (1, '70,11'), (0, '41,62'), (1, '61,42')}  # placeholder
common_p2c = {'50,11', '50,01'}
common_c2p = {'70,11', '70,01', '70,00'}

print("=== Non-routine frames (excluding common status/register reads) ===")
for ts, rel_ms, direction, op1, op2, payload in frames:
    opstr = f'{op1:02x},{op2:02x}'
    if direction == 0 and opstr in common_p2c:
        continue
    if direction == 1 and opstr in common_c2p:
        continue
    pay_hex = ' '.join(f'{b:02x}' for b in payload[:32])
    suffix = '...' if len(payload) > 32 else ''
    print(f"  +{rel_ms:6d}ms  {DIR_NAME[direction]:3s}  ({opstr})  [{len(payload)}B]  {pay_hex}{suffix}")
