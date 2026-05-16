"""Parse full GATT table from Evo Wide (19-51-52) HCI log, connection 1."""
import struct

LOG = r'f:\instax_evo_Protocol\captures\extracted\19-51-52\FS\data\log\bt\btsnoop_hci.log'


def parse_uuid128_le(b):
    b = bytes(reversed(b))
    return '%08x-%04x-%04x-%04x-%012x' % (
        int.from_bytes(b[0:4], 'big'), int.from_bytes(b[4:6], 'big'),
        int.from_bytes(b[6:8], 'big'), int.from_bytes(b[8:10], 'big'),
        int.from_bytes(b[10:16], 'big'),
    )


def prop_str(p):
    s = ''
    if p & 0x02: s += 'Read '
    if p & 0x04: s += 'WriteNoResp '
    if p & 0x08: s += 'Write '
    if p & 0x10: s += 'Notify '
    if p & 0x20: s += 'Indicate '
    return s.strip() or f'0x{p:02X}'


with open(LOG, 'rb') as f:
    f.read(16)
    idx = 0
    conn_h = 0x000A
    services = []
    chars = []
    cccds = []

    while True:
        hdr = f.read(24)
        if len(hdr) < 24:
            break
        orig, inc, flags, drops, ts = struct.unpack('>IIIIq', hdr)
        data = f.read(inc)
        idx += 1
        if not data or data[0] != 2 or len(data) < 9:
            continue
        acl_h = struct.unpack_from('<H', data, 1)[0] & 0x0FFF
        cid = struct.unpack_from('<H', data, 7)[0]
        if cid != 0x0004 or acl_h != conn_h:
            continue
        payload = data[9:]
        if not payload:
            continue
        op = payload[0]

        if op == 0x11 and (flags & 1):  # Read By Group Type RSP (services)
            il = payload[1]
            items = payload[2:]
            while len(items) >= il:
                it = items[:il]
                items = items[il:]
                s, e = struct.unpack_from('<HH', it)
                ub = it[4:]
                u = f'0x{struct.unpack_from("<H",ub)[0]:04X}' if len(ub) == 2 else parse_uuid128_le(ub)
                services.append((s, e, u))

        elif op == 0x09 and (flags & 1):  # Read By Type RSP (char declarations)
            il = payload[1]
            items = payload[2:]
            while len(items) >= il:
                it = items[:il]
                items = items[il:]
                if len(it) < 5:
                    break
                dh = struct.unpack_from('<H', it)[0]
                pr = it[2]
                vh = struct.unpack_from('<H', it, 3)[0]
                ub = it[5:]
                u = f'0x{struct.unpack_from("<H",ub)[0]:04X}' if len(ub) == 2 else parse_uuid128_le(ub)
                chars.append((dh, pr, vh, u))

        elif op == 0x05 and (flags & 1):  # Find Information RSP (descriptors)
            fmt = payload[1]
            items = payload[2:]
            if fmt == 1:  # 16-bit UUIDs
                while len(items) >= 4:
                    h = struct.unpack_from('<H', items)[0]
                    u = struct.unpack_from('<H', items, 2)[0]
                    if u == 0x2902:
                        cccds.append(h)
                    items = items[4:]

        # Stop when app protocol starts (first write to CCCD h=0x0013)
        if op == 0x12 and len(payload) >= 3:
            h = struct.unpack_from('<H', payload, 1)[0]
            if h == 0x0013:
                break

print('=== SERVICES ===')
for s, e, u in services:
    print(f'  h=0x{s:04X}-0x{e:04X}  {u}')

print()
print('=== CHARACTERISTICS ===')
# Map service for each char
svc_map = {}
for dh, pr, vh, u in chars:
    svc = next((f'h=0x{s:04X}-0x{e:04X}' for s, e, _ in services if s <= dh <= e), '?')
    svc_map[dh] = svc
    print(f'  decl=0x{dh:04X}  val=0x{vh:04X}  [{prop_str(pr):<25}]  {u}')
    print(f'    in service: {svc}')

print()
print('=== CCCDs (h=xxxx) ===')
for h in cccds:
    # Find which char this belongs to (descriptor follows value handle)
    owner = next(((dh, vh, u) for dh, pr, vh, u in chars if vh < h <= vh + 5), None)
    if owner:
        print(f'  CCCD h=0x{h:04X}  -> char val h=0x{owner[1]:04X} ({owner[2][:20]})')
    else:
        print(f'  CCCD h=0x{h:04X}')
