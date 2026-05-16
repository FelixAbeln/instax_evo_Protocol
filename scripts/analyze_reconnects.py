"""
Find all Instax reconnect sessions in the new log (18-49-41).
Show the first N ATT packets per connection handle to capture handshake.
Also decode 128-bit UUID GATT discovery for custom service.
"""
import struct
import pathlib
import datetime

BTSNOOP_EPOCH_DELTA = 0x00dcddb30f2f8000


def parse_btsnoop(path):
    data = pathlib.Path(path).read_bytes()
    if data[:8] != b'btsnoop\x00':
        return []
    pos = 16
    records = []
    while pos + 24 <= len(data):
        orig_len, inc_len, flags, drops, ts_be = struct.unpack_from(">IIIIq", data, pos)
        payload = data[pos + 24: pos + 24 + inc_len]
        records.append((ts_be, flags, payload))
        pos += 24 + inc_len
    return records


def ts_str(ts_us):
    unix_us = ts_us - BTSNOOP_EPOCH_DELTA
    s = unix_us // 1_000_000
    us = unix_us % 1_000_000
    try:
        dt = datetime.datetime.fromtimestamp(s, tz=datetime.timezone.utc)
        return dt.strftime("%H:%M:%S") + f".{us:06d}"
    except Exception:
        return f"ts={ts_us}"


def analyze(logname):
    path = f"captures/extracted/{logname}"
    print(f"\n{'='*70}")
    print(f"  {logname}")
    print(f"{'='*70}")
    records = parse_btsnoop(path)
    print(f"  Total records: {len(records)}")

    # ── Collect connection/disconnect events ──
    connections = {}  # handle → (ts, addr)
    disconnections = []
    for ts, flags, pkt in records:
        if flags != 3 or not pkt or pkt[0] != 0x04:
            continue
        evt = pkt[1]
        if evt == 0x3E and len(pkt) >= 5:
            sub = pkt[3]
            status = pkt[4]
            if sub == 0x01 and status == 0 and len(pkt) >= 20:
                handle = struct.unpack_from("<H", pkt, 5)[0]
                addr = ':'.join(f'{b:02X}' for b in reversed(pkt[9:15]))
                connections[handle] = (ts, addr)
            elif sub == 0x0A and status == 0 and len(pkt) >= 32:
                handle = struct.unpack_from("<H", pkt, 5)[0]
                addr = ':'.join(f'{b:02X}' for b in reversed(pkt[9:15]))
                connections[handle] = (ts, addr)
        elif evt == 0x05 and len(pkt) >= 7:
            handle = struct.unpack_from("<H", pkt, 4)[0]
            reason = pkt[6]
            disconnections.append((ts, handle, reason))

    # ── Find all ATT packets and group by connection handle ──
    # We care about Instax handles: 0x001d, 0x0020, 0x0027, 0x002a
    INSTAX_HANDLES = {0x001d, 0x0020, 0x0027, 0x002a}

    sessions = {}  # conn_handle → list of (ts, flags, att_op, att_handle, att_data)
    gatt_discovery_128 = {}  # val_handle → (props, uuid128)

    for ts, flags, pkt in records:
        if not pkt or pkt[0] != 0x02 or len(pkt) < 10:
            continue
        conn_h = struct.unpack_from("<H", pkt, 1)[0] & 0x0FFF
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:
            continue
        att_op = pkt[9]
        att_data = pkt[10:]

        # Collect 128-bit GATT characteristic declaration responses
        if att_op == 0x09 and len(att_data) >= 1:
            item_len = att_data[0]
            payload = att_data[1:]
            if item_len == 21:  # 128-bit UUID
                for i in range(0, len(payload) - 20, 21):
                    attr_h = struct.unpack_from("<H", payload, i)[0]
                    props = payload[i + 2]
                    val_h = struct.unpack_from("<H", payload, i + 3)[0]
                    uuid128 = payload[i + 5: i + 21]
                    gatt_discovery_128[val_h] = (props, uuid128.hex())
                    print(f"  [GATT 128b] attr={attr_h:#06x} props={props:#04x} "
                          f"val={val_h:#06x} uuid={uuid128[:4].hex()}...")

        # Collect CCCD writes (h=0x001e, 0x0028 = Instax CCCDs)
        att_handle = None
        if len(att_data) >= 2:
            att_handle = struct.unpack_from("<H", att_data, 0)[0]

        is_instax = att_handle in INSTAX_HANDLES if att_handle else False
        is_cccd = att_handle in {0x001e, 0x0028} if att_handle else False

        if is_instax or is_cccd:
            if conn_h not in sessions:
                sessions[conn_h] = []
            sessions[conn_h].append((ts, flags, att_op, att_handle, att_data))

    # ── Print sessions ──
    op_names = {
        0x12: "WR_REQ", 0x13: "WR_RSP", 0x52: "WR_CMD",
        0x1B: "NOTIFY", 0x1D: "INDICATE",
        0x02: "MTU_REQ", 0x03: "MTU_RSP",
    }

    print(f"\n--- Sessions with Instax traffic ({len(sessions)} connection handles) ---")
    for conn_h in sorted(sessions.keys()):
        pkts = sessions[conn_h]
        conn_info = connections.get(conn_h)
        conn_ts = ts_str(conn_info[0]) if conn_info else "pre-log"
        conn_addr = conn_info[1] if conn_info else "???"
        disc = next((d for d in disconnections if d[1] == conn_h), None)
        disc_ts = ts_str(disc[0]) if disc else "still connected"

        print(f"\n  ┌─ conn=0x{conn_h:04X}  connected={conn_ts}  addr={conn_addr}  "
              f"disconnected={disc_ts}  packets={len(pkts)}")

        shown = 0
        for ts, flags, att_op, att_handle, att_data in pkts:
            if shown >= 50:
                print(f"  │  ... ({len(pkts) - shown} more packets)")
                break
            direction = "host→dev" if flags == 2 else "dev→host"
            op_name = op_names.get(att_op, f"0x{att_op:02X}")
            value = att_data[2:].hex() if len(att_data) > 2 else ""
            print(f"  │  {ts_str(ts)} {direction} {op_name} h={att_handle:#06x}  "
                  f"[{len(att_data)-2}] {value[:80]}")
            shown += 1
        print(f"  └─ end")

    # ── Print 128-bit UUID GATT discoveries found ──
    if gatt_discovery_128:
        print(f"\n--- 128-bit GATT characteristic discoveries ---")
        for val_h, (props, uuid) in sorted(gatt_discovery_128.items()):
            prop_names = []
            if props & 0x02: prop_names.append("read")
            if props & 0x04: prop_names.append("write-no-rsp")
            if props & 0x08: prop_names.append("write")
            if props & 0x10: prop_names.append("notify")
            if props & 0x20: prop_names.append("indicate")
            print(f"  val_h={val_h:#06x}  props=[{','.join(prop_names)}]  uuid={uuid}")
    else:
        print("\n  (no 128-bit GATT characteristic discoveries captured)")

    # ── Show ALL CCCD subscription writes ──
    print("\n--- CCCD subscription writes (notify enable) ---")
    for ts, flags, pkt in records:
        if not pkt or pkt[0] != 0x02 or len(pkt) < 14:
            continue
        l2cap_cid = struct.unpack_from("<H", pkt, 7)[0]
        if l2cap_cid != 0x0004:
            continue
        att_op = pkt[9]
        if att_op in (0x12, 0x52) and len(pkt) >= 14:
            att_h = struct.unpack_from("<H", pkt, 10)[0]
            val = pkt[12:]
            if att_h in {0x001e, 0x0028, 0x0004, 0x000a, 0x0018}:
                conn_h = struct.unpack_from("<H", pkt, 1)[0] & 0x0FFF
                direction = "host→dev" if flags == 2 else "dev→host"
                print(f"  {ts_str(ts)} conn={conn_h:#06x} {direction} "
                      f"CCCD-write h={att_h:#06x} val={val.hex()}")


if __name__ == "__main__":
    for logfile in ["btsnoop_hci__18-49-41.log", "btsnoop_hci.log__18-49-41.last"]:
        analyze(logfile)
