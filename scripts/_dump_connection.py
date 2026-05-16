"""
Dump the first 100 HCI records (all types) to find connection events and addresses.
"""
import struct

LOG = r"f:\instax_evo_Protocol\captures\extracted\bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-17-34-32__btsnoop_hci.log"

def parse_btsnoop(path):
    with open(path, "rb") as f:
        magic = f.read(8)
        assert magic == b"btsnoop\x00"
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

records = parse_btsnoop(LOG)
print(f"Total: {len(records)} records")

for i, (flags, ts, data) in enumerate(records[:120]):
    if len(data) < 2:
        continue
    hci_type = data[0]
    direction = "HOST→CTRL" if (flags & 1) == 0 else "CTRL→HOST"
    
    if hci_type == 4:  # HCI Event
        evcode = data[1] if len(data) > 1 else 0
        if evcode == 0x3e and len(data) > 3:  # LE Meta
            sub = data[3]
            if sub == 0x01:  # LE Connection Complete
                print(f"[{i:4d}] {direction} HCI_LE_Connection_Complete: {data.hex()}")
                # decode: sub(1), status(1), conn_handle(2), role(1), peer_addr_type(1), peer_addr(6), ...
                if len(data) >= 16:
                    status = data[4]
                    handle = struct.unpack_from("<H", data, 5)[0]
                    role = data[7]
                    addr_type = data[8]
                    addr = data[9:15]
                    addr_str = ":".join(f"{b:02X}" for b in reversed(addr))
                    print(f"         status={status} handle={handle:#06x} role={'CENTRAL' if role==0 else 'PERIPHERAL'} addr_type={addr_type} addr={addr_str}")
            elif sub == 0x0a:  # LE Enhanced Connection Complete
                print(f"[{i:4d}] {direction} HCI_LE_Enhanced_Connection_Complete: {data.hex()}")
            elif sub in (0x02, 0x04, 0x05, 0x13):
                print(f"[{i:4d}] {direction} LE_Meta sub=0x{sub:02x}: {data[:20].hex()}")
        elif evcode == 0x03:  # Connection Complete (BR/EDR)
            print(f"[{i:4d}] {direction} HCI_Connection_Complete: {data.hex()}")
            if len(data) >= 13:
                status = data[2]
                handle = struct.unpack_from("<H", data, 3)[0]
                addr = data[5:11]
                addr_str = ":".join(f"{b:02X}" for b in reversed(addr))
                print(f"         status={status} handle={handle:#06x} addr={addr_str}")
        elif evcode == 0x0e:  # Command Complete
            pass  # too common, skip
        elif evcode in (0x05, 0x08, 0x13, 0x18, 0x19, 0x30):
            print(f"[{i:4d}] {direction} Event 0x{evcode:02x}: {data[:20].hex()}")
        else:
            print(f"[{i:4d}] {direction} Event 0x{evcode:02x}: {data[:16].hex()}")
    elif hci_type == 2:  # ACL
        if i < 20:
            handle_raw = struct.unpack_from("<H", data, 1)[0]
            conn = handle_raw & 0x0FFF
            if len(data) >= 8:
                cid = struct.unpack_from("<H", data, 7)[0]
                print(f"[{i:4d}] {direction} ACL conn={conn:#06x} CID={cid:#06x} len={data[3]} raw={data[:12].hex()}")
