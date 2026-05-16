"""
Dump the complete handshake and status exchange from the 17-34-32 log.
Focus on the first 150 ATT packets after connection, showing raw hex.
"""
import struct, sys

LOG = r"f:\instax_evo_Protocol\captures\extracted\bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-34-32__btsnoop_hci.log"

def parse_btsnoop(path):
    with open(path, "rb") as f:
        magic = f.read(8)
        assert magic == b"btsnoop\x00", f"Bad magic: {magic!r}"
        ver, datalink = struct.unpack(">II", f.read(8))
        records = []
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig, inc, flags, drops, ts_us = struct.unpack(">IIIIq", hdr)
            data = f.read(inc)
            records.append((flags, ts_us, data))
    return records

def att_opcode_name(op):
    names = {
        0x01:"Error",0x02:"ExchangeMTUReq",0x03:"ExchangeMTURsp",
        0x04:"FindInfoReq",0x05:"FindInfoRsp",0x06:"FindByTypeReq",
        0x08:"ReadByTypeReq",0x09:"ReadByTypeRsp",0x0a:"ReadReq",
        0x0b:"ReadRsp",0x0c:"ReadBlobReq",0x12:"WriteReq",0x13:"WriteRsp",
        0x16:"PrepWriteReq",0x18:"ExecWriteReq",0x1b:"Notification",
        0x52:"WriteCmd",0x1d:"Indication",0x1e:"Confirmation",
        0x10:"ReadByGroupReq",0x11:"ReadByGroupRsp",
    }
    return names.get(op, f"0x{op:02x}")

records = parse_btsnoop(LOG)
print(f"Total records: {len(records)}")

# Find LE connection events and ATT traffic
att_count = 0
for flags, ts, data in records:
    if len(data) < 2:
        continue
    hci_type = data[0]
    
    # HCI event = type 4
    if hci_type == 4 and len(data) > 3:
        evcode = data[1]
        if evcode == 0x3e:  # LE Meta
            sub = data[3]
            if sub == 0x01:  # LE Connection Complete
                print(f"\n*** LE Connection Complete at ts={ts} ***")
                print(f"    {data.hex()}")
                continue
    
    # ACL = type 2
    if hci_type != 2:
        continue
    if len(data) < 9:
        continue

    # Parse ACL: handle(2), total_len(2), l2cap_len(2), cid(2), payload
    handle_raw = struct.unpack_from("<H", data, 1)[0]
    conn_handle = handle_raw & 0x0FFF
    l2cap_len = struct.unpack_from("<H", data, 5)[0]
    cid = struct.unpack_from("<H", data, 7)[0]
    payload = data[9:]

    if cid != 0x0004:  # ATT CID
        continue
    if len(payload) < 1:
        continue

    op = payload[0]
    direction = "host→dev" if (flags & 1) == 0 else "dev→host"

    # Extract handle from ATT packet (bytes 1-2 for most packets)
    att_handle = None
    if len(payload) >= 3 and op in (0x12, 0x52, 0x1b, 0x1d, 0x0a, 0x0b, 0x13, 0x01):
        att_handle = struct.unpack_from("<H", payload, 1)[0]

    handle_str = f"h=0x{att_handle:04X}" if att_handle else ""
    print(f"[{att_count:4d}] {direction}  op={att_opcode_name(op):<18s} {handle_str}  [{len(payload)}] {payload.hex()}")
    att_count += 1
    if att_count >= 150:
        break

print(f"\nTotal ATT packets shown: {att_count}")
