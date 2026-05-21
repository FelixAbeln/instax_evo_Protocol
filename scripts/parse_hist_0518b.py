"""Parse HIST_GET_DATA responses from the 21:39 bugreport btsnoop log."""
import struct

LOG = r'c:\Users\Compf\Downloads\Phone Link\bugreport_0518b\FS\data\log\bt\btsnoop_hci.log.last'
BTSNOOP_MAGIC = b'btsnoop\x00'


def iter_packets(path):
    with open(path, 'rb') as f:
        hdr = f.read(16)
        assert hdr[:8] == BTSNOOP_MAGIC
        while True:
            rec_hdr = f.read(24)
            if len(rec_hdr) < 24:
                break
            orig_len, inc_len, flags, drops, ts = struct.unpack('>IIIIQ', rec_hdr)
            data = f.read(inc_len)
            if len(data) < inc_len:
                break
            yield flags, data


def extract_att_payload(acl):
    if len(acl) < 9:
        return None, None
    # ACL: [0:2]=handle, [2:4]=acl_data_len, [4:6]=l2cap_pdu_len, [6:8]=l2cap_cid, [8:]=att
    cid = struct.unpack_from('<H', acl, 6)[0]
    if cid != 0x0004:
        return None, None
    att = acl[8:]
    return True, att


buf = bytearray()
records = []

for flags, pkt in iter_packets(LOG):
    if len(pkt) < 2:
        continue
    if pkt[0] != 0x02:  # ACL only
        continue
    acl = pkt[1:]
    ok, att = extract_att_payload(acl)
    if not ok:
        continue
    att_op = att[0]
    if att_op not in (0x1B, 0x1D):
        continue
    if len(att) < 4:
        continue
    att_val = att[3:]

    if len(att_val) >= 2 and att_val[0] == 0x61 and att_val[1] == 0x42:
        buf = bytearray(att_val)
    elif len(buf) >= 4 and buf[0] == 0x61 and buf[1] == 0x42:
        buf.extend(att_val)
    else:
        continue

    if len(buf) < 6:
        continue
    if buf[0] != 0x61 or buf[1] != 0x42:
        continue
    total = struct.unpack_from('>H', buf, 2)[0]
    if len(buf) < total:
        continue

    frame = bytes(buf[:total])
    buf = bytearray()
    op1, op2 = frame[4], frame[5]
    payload = frame[6:total - 1]

    if op1 == 0x84 and op2 == 0x0a:
        records.append(payload)

print(f"Total HIST_GET_DATA (84,0a) responses found: {len(records)}")
print()

for i, payload in enumerate(records):
    if len(payload) < 14:
        print(f"Response {i}: too short ({len(payload)}B)")
        continue

    # Layout: [6B zeros][8B ASCII date "YYYYMMDD"][N × 44B records]
    date_str = payload[6:14].decode('ascii', 'replace')
    rec_data = payload[14:]
    rec_size = 44
    n_recs = len(rec_data) // rec_size
    print(f"Response {i}: date={date_str}  shots={n_recs}  ({n_recs} records x {rec_size}B)")

    for r in range(n_recs):
        rec = rec_data[r * rec_size:(r + 1) * rec_size]
        nz = [(b, hex(rec[b])) for b in range(len(rec)) if rec[b] != 0]
        if nz:
            hex_str = ' '.join(f'{x:02x}' for x in rec)
            print(f"  rec[{r:2d}]: {hex_str}")
            print(f"         non-zero: {nz}")
    print()
