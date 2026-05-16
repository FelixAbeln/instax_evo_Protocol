"""
Analyze the new camera model HCI log.
Dumps Write/Notification packets from all INSTAX connections,
comparing cmd=0x02 (status) and cmd=0x80/0x11 (registers) responses
across connections to identify battery and image count fields.
"""
import struct, sys

LOG_MAIN = r'f:\instax_evo_Protocol\captures\extracted\19-51-52\FS\data\log\bt\btsnoop_hci.log'

def decode_body(raw):
    """Decode Instax TLV body: 41 62 00 (REQ) or 61 42 00 (RSP)."""
    if len(raw) < 6:
        return raw.hex()
    if raw[:3] == b'\x41\x62\x00':
        tag = 'REQ'
    elif raw[:3] == b'\x61\x42\x00':
        tag = 'RSP'
    else:
        return raw.hex()[:80]
    length = struct.unpack_from('<H', raw, 3)[0]
    cmd  = raw[5] if len(raw) > 5 else 0
    sub  = raw[6] if len(raw) > 6 else 0
    rest = raw[7:].hex() if len(raw) > 7 else ''
    return f'{tag} len={length:3d} cmd=0x{cmd:02X} sub=0x{sub:02X}  {rest[:60]}'


def analyze_conn(log_path, conn_index=1, max_packets=70):
    """Dump Write/Notification packets from connection #conn_index (1-based)."""
    print(f'\n=== Connection {conn_index} (first {max_packets} Write/Notify) ===')
    conn_no   = 0
    target_h  = None
    started   = False
    att_idx   = 0

    with open(log_path, 'rb') as f:
        f.read(16)  # btsnoop header
        idx = 0
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig, inc, flags, drops, ts = struct.unpack('>IIIIq', hdr)
            data = f.read(inc)
            idx += 1
            if not data:
                continue

            # LE Enhanced Connection Complete
            if (data[0] == 4 and len(data) > 9 and
                    data[1] == 0x3e and data[3] == 0x0a):
                s = data[4]
                h = struct.unpack_from('<H', data, 5)[0]
                peer = ':'.join(f'{b:02X}' for b in reversed(data[9:15]))
                if s == 0 and 'FA:AB:BC' in peer:
                    conn_no += 1
                    if conn_no == conn_index:
                        target_h = h
                        started  = True
                        print(f'  LE conn h=0x{h:04X} to {peer} @ log record {idx}')

            if not started:
                continue

            # ACL, ATT only
            if data[0] != 2 or len(data) < 9:
                continue
            acl_h = struct.unpack_from('<H', data, 1)[0] & 0x0FFF
            cid   = struct.unpack_from('<H', data, 7)[0]
            if cid != 0x0004 or acl_h != target_h:
                continue

            payload = data[9:]
            if not payload or payload[0] not in (0x52, 0x12, 0x1b):
                continue

            op        = payload[0]
            direction = 'R' if flags & 1 else 'S'
            att_idx  += 1

            if att_idx > max_packets:
                print(f'  ... stopped at {max_packets}')
                break

            h_val = struct.unpack_from('<H', payload, 1)[0] if len(payload) >= 3 else 0
            body  = payload[3:]
            dec   = decode_body(body)
            op_c  = 'Wr' if op in (0x52, 0x12) else 'Nt'
            print(f'  [{att_idx:3d}]{direction}{op_c} h=0x{h_val:04X}  {dec}')


if __name__ == '__main__':
    for i in range(1, 5):
        analyze_conn(LOG_MAIN, conn_index=i, max_packets=55)
