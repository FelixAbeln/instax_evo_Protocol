"""
Analyze the new HCI bugreport logs (18-49-41).
Specifically looking for:
 - Connections to INSTAX (Android) vs INSTAX (IOS)
 - Full session GATT + write sequence for each reconnect
"""
import struct
import pathlib
import datetime

BTSNOOP_EPOCH_DELTA = 0x00dcddb30f2f8000  # microseconds from year 0 to Unix epoch 1970


def parse_btsnoop(path):
    data = pathlib.Path(path).read_bytes()
    if data[:8] != b'btsnoop\x00':
        print(f"Not a btsnoop file: {path}")
        return []
    pos = 16
    records = []
    while pos + 24 <= len(data):
        orig_len, inc_len, flags, drops, ts_be = struct.unpack_from(">IIIIq", data, pos)
        payload = data[pos + 24 : pos + 24 + inc_len]
        records.append((ts_be, flags, payload))
        pos += 24 + inc_len
    return records


def ts_str(ts_us):
    unix_us = ts_us - BTSNOOP_EPOCH_DELTA
    s = unix_us // 1_000_000
    us = unix_us % 1_000_000
    try:
        dt = datetime.datetime.utcfromtimestamp(s)
        return dt.strftime("%H:%M:%S") + f".{us:06d}"
    except Exception:
        return f"ts={ts_us}"


def addr_str(b6):
    return ":".join(f"{b:02X}" for b in reversed(b6))


def analyze(logname):
    path = f"captures/extracted/{logname}"
    print(f"\n{'='*60}")
    print(f"  {logname}")
    print(f"{'='*60}")
    records = parse_btsnoop(path)
    print(f"  Total records: {len(records)}")
    if not records:
        return

    # ── Pass 1: find all LE connection/disconnect events ──
    print("\n--- Connection events ---")
    conn_handles = {}  # handle → (ts, addr)
    for ts, flags, pkt in records:
        if flags != 3 or not pkt or pkt[0] != 0x04:
            continue
        evt = pkt[1]
        if evt == 0x3E and len(pkt) >= 5:
            sub = pkt[3]
            status = pkt[4]
            if sub == 0x01 and status == 0 and len(pkt) >= 20:  # LE Conn Complete
                handle = struct.unpack_from("<H", pkt, 5)[0]
                addr = addr_str(pkt[8:14])
                conn_handles[handle] = (ts, addr)
                print(f"  CONNECT    @ {ts_str(ts)}  handle={handle:#06x}  addr={addr}")
            elif sub == 0x0A and status == 0 and len(pkt) >= 32:  # LE Enhanced Conn Complete
                handle = struct.unpack_from("<H", pkt, 5)[0]
                addr = addr_str(pkt[9:15])
                conn_handles[handle] = (ts, addr)
                print(f"  CONNECT(E) @ {ts_str(ts)}  handle={handle:#06x}  addr={addr}")
        elif evt == 0x05 and len(pkt) >= 7:  # Disconnection Complete
            handle = struct.unpack_from("<H", pkt, 4)[0]
            reason = pkt[6]
            print(f"  DISCONNECT @ {ts_str(ts)}  handle={handle:#06x}  reason={reason:#04x}")

    if not conn_handles:
        print("  (no LE connection events found — log may start mid-session)")

    # ── Pass 2: find ATT packets and Device Name reads ──
    print("\n--- ATT packets (first 20, looking for device name) ---")
    att_count = 0
    device_names = {}  # addr → name (from ATT Read Response for device name h=0x0006)
    for ts, flags, pkt in records:
        if len(pkt) < 10:
            continue
        # HCI ACL: pkt[0]=0x02
        if pkt[0] != 0x02:
            continue
        conn_handle = struct.unpack_from("<H", pkt, 1)[0] & 0x0FFF
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:  # not ATT
            continue
        att_op = pkt[9]
        att_count += 1

        # ATT Read Response (0x0B) - might be device name
        if att_op == 0x0B and len(pkt) > 10:
            val = pkt[10:]
            try:
                name = val.decode("utf-8", errors="replace").rstrip("\x00")
                if "INSTAX" in name.upper():
                    addr = conn_handles.get(conn_handle, (None, "???"))[1]
                    print(f"  Device Name @ {ts_str(ts)}  conn={conn_handle:#06x}  addr={addr}  name={name!r}")
                    device_names[conn_handle] = name
            except Exception:
                pass

    print(f"  Total ATT records: {att_count}")

    # ── Pass 3: find all ATT Write Commands/Requests to Instax service ──
    # The Instax write char may be at any handle; look for writes after device name resolved
    # Look for all ATT write ops: 0x52 (Write Command), 0x12 (Write Request)
    # and all ATT Handle Value Notifications: 0x1B
    print("\n--- ATT Writes and Notifications (Instax sessions) ---")
    instax_handles = set()  # likely write handles used in an Instax session

    # First collect all write target handles
    write_handles = {}  # handle → count
    for ts, flags, pkt in records:
        if pkt[0:1] != b"\x02" or len(pkt) < 12:
            continue
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:
            continue
        att_op = pkt[9]
        if att_op in (0x52, 0x12) and len(pkt) >= 12:
            att_handle = struct.unpack_from("<H", pkt, 10)[0]
            write_handles[att_handle] = write_handles.get(att_handle, 0) + 1

    print(f"  Write target handles found: { {hex(h): c for h, c in sorted(write_handles.items())} }")

    notify_handles = {}
    for ts, flags, pkt in records:
        if pkt[0:1] != b"\x02" or len(pkt) < 12:
            continue
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:
            continue
        att_op = pkt[9]
        if att_op == 0x1B and len(pkt) >= 12:  # Handle Value Notification
            att_handle = struct.unpack_from("<H", pkt, 10)[0]
            notify_handles[att_handle] = notify_handles.get(att_handle, 0) + 1

    print(f"  Notify source handles found:  { {hex(h): c for h, c in sorted(notify_handles.items())} }")

    # ── Pass 4: Find GATT characteristic discovery responses ──
    # ATT Read By Type Response (0x09) - used during GATT char discovery
    # ATT Find By Type Value Response (0x07) - used during service discovery
    print("\n--- GATT Discovery (characteristic discovery responses) ---")
    gatt_chars = {}  # handle → (props, value_handle, uuid_bytes)
    for ts, flags, pkt in records:
        if pkt[0:1] != b"\x02" or len(pkt) < 10:
            continue
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:
            continue
        att_op = pkt[9]
        # ATT Read By Type Response (0x09) — characteristic declarations
        if att_op == 0x09 and len(pkt) >= 11:
            item_len = pkt[10]
            payload = pkt[11:]
            if item_len == 7:  # 16-bit UUID char declaration
                for i in range(0, len(payload) - 6, 7):
                    attr_handle = struct.unpack_from("<H", payload, i)[0]
                    props = payload[i + 2]
                    val_handle = struct.unpack_from("<H", payload, i + 3)[0]
                    uuid16 = struct.unpack_from("<H", payload, i + 5)[0]
                    print(f"    Char decl: attr={attr_handle:#06x} props={props:#04x} value_h={val_handle:#06x} uuid16={uuid16:#06x}")
                    gatt_chars[val_handle] = (props, uuid16)
            elif item_len == 21:  # 128-bit UUID char declaration
                for i in range(0, len(payload) - 20, 21):
                    attr_handle = struct.unpack_from("<H", payload, i)[0]
                    props = payload[i + 2]
                    val_handle = struct.unpack_from("<H", payload, i + 3)[0]
                    uuid128 = payload[i + 5 : i + 21].hex()
                    print(f"    Char decl: attr={attr_handle:#06x} props={props:#04x} value_h={val_handle:#06x} uuid128={uuid128[:8]}...")
                    gatt_chars[val_handle] = (props, uuid128)

    # ── Pass 5: Show first 60 ATT packets in detail for Instax service ──
    print("\n--- First 80 ATT packets (detailed) ---")
    n = 0
    for ts, flags, pkt in records:
        if pkt[0:1] != b"\x02" or len(pkt) < 10:
            continue
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:
            continue
        n += 1
        if n > 80:
            break
        conn_h = struct.unpack_from("<H", pkt, 1)[0] & 0x0FFF
        att_op = pkt[9]
        att_data = pkt[10:]

        direction = "host→dev" if flags == 2 else "dev→host"
        att_handle = None
        if len(att_data) >= 2 and att_op in (0x12, 0x52, 0x0A, 0x0B, 0x1B, 0x1D, 0x13):
            att_handle = struct.unpack_from("<H", att_data, 0)[0]

        op_names = {
            0x01: "ATT_ERR", 0x02: "MTU_REQ", 0x03: "MTU_RSP",
            0x04: "FIND_INFO_REQ", 0x05: "FIND_INFO_RSP",
            0x06: "FIND_TYPE_REQ", 0x07: "FIND_TYPE_RSP",
            0x08: "READ_TYPE_REQ", 0x09: "READ_TYPE_RSP",
            0x0A: "READ_REQ", 0x0B: "READ_RSP",
            0x0C: "READ_BLOB_REQ", 0x0D: "READ_BLOB_RSP",
            0x10: "READ_MULT_REQ", 0x11: "READ_MULT_RSP",
            0x12: "WRITE_REQ", 0x13: "WRITE_RSP",
            0x16: "PREP_WRITE_REQ", 0x17: "PREP_WRITE_RSP",
            0x18: "EXEC_WRITE_REQ", 0x19: "EXEC_WRITE_RSP",
            0x1B: "NOTIFY", 0x1D: "INDICATE",
            0x1E: "INDICATE_CONF",
            0x52: "WRITE_CMD",
        }
        op_name = op_names.get(att_op, f"0x{att_op:02X}")
        h_str = f" h={att_handle:#06x}" if att_handle is not None else ""
        print(f"  [{n:3d}] {ts_str(ts)} conn={conn_h:#06x} {direction} {op_name}{h_str}  [{len(att_data)}] {att_data.hex()[:60]}")


if __name__ == "__main__":
    for logfile in ["btsnoop_hci__18-49-41.log", "btsnoop_hci.log__18-49-41.last"]:
        analyze(logfile)
