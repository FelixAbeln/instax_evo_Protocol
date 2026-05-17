"""
Decode a full Instax Link print session from an Android btsnoop HCI log.

- Reassembles HCI ACL fragments into complete L2CAP/ATT packets
- Decodes every Link protocol opcode (41 62 / 61 42 framing)
- Summarises the print pipeline and highlights opcodes NOT in our implementation

Usage:
    python scripts/decode_print_flow.py [path_to_btsnoop.log]

Defaults to the 2026-05-17 .last.log capture.
"""
import struct
import sys
from pathlib import Path

# ── Log path ────────────────────────────────────────────────────────────────
DEFAULT_LOG = Path(
    "captures/extracted/2026-05-17/"
    "bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-17-10-02-52__btsnoop_hci.log.last.log"
)
LOG = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LOG

# ── Opcode names ─────────────────────────────────────────────────────────────
LINK_OPS = {
    (0x00, 0x00): "SUPPORT_FUNCTION_AND_VERSION_INFO",
    (0x00, 0x01): "DEVICE_INFO_SERVICE",
    (0x00, 0x02): "SUPPORT_FUNCTION_INFO",
    (0x00, 0x10): "IDENTIFY_INFORMATION",
    (0x01, 0x00): "SHUT_DOWN",
    (0x01, 0x02): "AUTO_SLEEP_SETTINGS",
    (0x10, 0x00): "PRINT_IMAGE_DOWNLOAD_START",
    (0x10, 0x01): "PRINT_IMAGE_DOWNLOAD_DATA",
    (0x10, 0x02): "PRINT_IMAGE_DOWNLOAD_END",
    (0x10, 0x80): "PRINT_IMAGE",
    (0x10, 0x81): "REJECT_FILM_COVER",
    (0x20, 0x00): "FW_DOWNLOAD_START",
    (0x20, 0x10): "FW_PROGRAM_INFO",
    (0x30, 0x00): "XYZ_AXIS_INFO",
    (0x30, 0x01): "LED_PATTERN_SETTINGS",
    (0x80, 0x00): "CAMERA_SETTINGS",
    (0x80, 0x01): "CAMERA_SETTINGS_GET",
    (0x80, 0x10): "CAM_REG_BANK",
    (0x80, 0x11): "CAM_REG_READ",
    (0x84, 0x00): "CAMERA_LOG_SUBTOTAL_START",
    (0x84, 0x01): "CAMERA_LOG_SUBTOTAL_DATA",
    (0x84, 0x02): "CAMERA_LOG_SUBTOTAL_CLEAR",
    (0x84, 0x03): "CAMERA_LOG_DATE_START",
    (0x84, 0x04): "CAMERA_LOG_DATE_DATA",
    (0x84, 0x05): "CAMERA_LOG_DATE_END",
    (0x84, 0x06): "CAMERA_LOG_FILTER_START",
    (0x84, 0x07): "CAMERA_LOG_FILTER_DATA",
    (0x84, 0x08): "CAMERA_LOG_FILTER_END",
}

# Opcodes our evo-print tool currently sends
OUR_OPS = {
    (0x00, 0x00), (0x00, 0x01), (0x00, 0x02),
    (0x10, 0x00), (0x10, 0x01), (0x10, 0x02), (0x10, 0x80),
}

INFO_TYPES = {
    0x00: "IMAGE_SUPPORT_INFO",
    0x01: "BATTERY_INFO",
    0x02: "PRINTER_FUNCTION_INFO",
    0x03: "PRINT_HISTORY_INFO",
    0x04: "CAMERA_FUNCTION_INFO",
    0x05: "CAMERA_HISTORY_INFO",
}


# ── btsnoop parser ────────────────────────────────────────────────────────────
def parse_btsnoop_raw(path: Path):
    """Yield (ts_sec, flags, hci_data) for every record."""
    with open(path, "rb") as f:
        hdr = f.read(16)
        if hdr[:8] != b"btsnoop\x00":
            raise ValueError("Not a btsnoop file")
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec[:16])
            ts_us = struct.unpack(">q", rec[16:])[0]
            ts_sec = (ts_us - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            yield ts_sec, flags, data


def reassemble_acl(path: Path):
    """
    Yield (ts_sec, direction, acl_handle, l2cap_cid, att_payload) for complete
    reassembled L2CAP packets.  Handles HCI ACL fragmentation.
    direction: 'host' or 'dev'
    """
    # per-direction reassembly buffers keyed by acl_handle
    bufs: dict[tuple, bytearray] = {}   # (direction, acl_handle) → partial l2cap

    for ts, flags, data in parse_btsnoop_raw(path):
        if not data or data[0] != 0x02 or len(data) < 5:
            continue
        hdr16 = struct.unpack_from("<H", data, 1)[0]
        acl_handle = hdr16 & 0x0FFF
        pb = (hdr16 >> 12) & 0x3       # 0b10 = first, 0b01 = continuing
        direction = "dev" if flags & 1 else "host"
        key = (direction, acl_handle)
        hci_payload = data[5:]          # everything after the 4-byte HCI header + 1 indicator

        if pb == 0x2 or pb == 0x0:     # start of new packet
            if len(hci_payload) < 4:
                continue
            l2cap_len = struct.unpack_from("<H", hci_payload, 0)[0]
            cid       = struct.unpack_from("<H", hci_payload, 2)[0]
            body      = hci_payload[4:]
            buf = bytearray(body)
            if len(buf) >= l2cap_len:
                yield ts, direction, acl_handle, cid, bytes(buf[:l2cap_len])
            else:
                bufs[key] = (ts, cid, l2cap_len, buf)
        elif pb == 0x1:                 # continuation
            if key not in bufs:
                continue
            first_ts, cid, l2cap_len, buf = bufs[key]
            buf.extend(hci_payload)
            if len(buf) >= l2cap_len:
                yield first_ts, direction, acl_handle, cid, bytes(buf[:l2cap_len])
                del bufs[key]
            else:
                bufs[key] = (first_ts, cid, l2cap_len, buf)


# ── Link packet reassembler (multi-BLE-write fragments) ──────────────────────
def decode_link_packets(path: Path):
    """
    Yield (ts, direction, op1, op2, payload) for every complete Link protocol
    packet found on ATT CID 0x0004.  Handles multi-write fragmentation.
    """
    # per (direction, acl_handle, att_handle) reassembly
    link_bufs: dict = {}

    for ts, direction, acl_handle, cid, att in reassemble_acl(path):
        if cid != 0x0004 or len(att) < 3:
            continue
        att_op = att[0]
        if att_op not in (0x52, 0x1B, 0x12):   # WriteNoResp, Notify, WriteReq
            continue
        att_handle = struct.unpack_from("<H", att, 1)[0]
        value = att[3:]

        key = (direction, acl_handle, att_handle)

        # Check if this is start of a Link packet
        if value[:2] in (b'\x41\x62', b'\x61\x42'):   # 'Ab' or 'aB'
            if len(value) < 4:
                continue
            total_len = struct.unpack_from(">H", value, 2)[0]
            if len(value) >= total_len:
                pkt = value[:total_len]
                if not _valid_cs(pkt):
                    continue
                op1, op2 = pkt[4], pkt[5]
                payload  = pkt[6:total_len - 1]
                yield ts, direction, op1, op2, payload
            else:
                link_bufs[key] = (ts, bytearray(value), total_len)
        elif key in link_bufs:
            # continuation of a fragmented Link packet
            first_ts, buf, total_len = link_bufs[key]
            buf.extend(value)
            if len(buf) >= total_len:
                pkt = bytes(buf[:total_len])
                del link_bufs[key]
                if not _valid_cs(pkt):
                    continue
                op1, op2 = pkt[4], pkt[5]
                payload  = pkt[6:total_len - 1]
                yield first_ts, direction, op1, op2, payload
            else:
                link_bufs[key] = (first_ts, buf, total_len)


def _valid_cs(pkt: bytes) -> bool:
    return len(pkt) >= 7 and (sum(pkt) & 0xFF) == 0xFF


# ── Pretty-print helpers ─────────────────────────────────────────────────────
def op_label(op1, op2):
    name = LINK_OPS.get((op1, op2), f"UNKNOWN(0x{op1:02X},0x{op2:02X})")
    marker = "" if (op1, op2) in OUR_OPS else "  ← NEW"
    return name, marker


def payload_detail(op1, op2, payload: bytes) -> str:
    if op1 == 0x00 and op2 in (0x01, 0x02) and len(payload) >= 1:
        it = INFO_TYPES.get(payload[0], f"InfoType=0x{payload[0]:02X}")
        return f"  [{it}]"
    if op1 == 0x10 and op2 == 0x00 and len(payload) >= 4:
        size = struct.unpack_from(">I", payload, 0)[0]
        return f"  img_size={size}B ({size/1024:.1f}KB)"
    if op1 == 0x10 and op2 == 0x01 and len(payload) >= 4:
        seq = struct.unpack_from(">I", payload, 0)[0]
        return f"  chunk_seq={seq}"
    if op1 == 0x10 and op2 == 0x80 and len(payload) >= 2:
        return f"  status=0x{payload[0]:02X} 0x{payload[1]:02X}"
    if op1 == 0x80 and op2 == 0x11 and len(payload) >= 1:
        return f"  reg=0x{payload[0]:02X}"
    if op1 == 0x84 and op2 == 0x00 and len(payload) >= 12:
        total = struct.unpack_from("<I", payload, 4)[0]
        return f"  total_shots={total}"
    return f"  payload={payload.hex()[:48]}"


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Log: {LOG}")
    print(f"Exists: {LOG.exists()}  Size: {LOG.stat().st_size/1024/1024:.1f} MB\n")

    packets = list(decode_link_packets(LOG))
    if not packets:
        print("No Link packets found.")
        return

    base = packets[0][0]
    print(f"Found {len(packets)} Link packets over {packets[-1][0]-base:.1f}s\n")

    # ── Collapsed view (deduplicate DATA chunks) ─────────────────────────────
    print("=" * 80)
    print("FULL PRINT FLOW (collapsed — DATA chunks summarised)")
    print("=" * 80)

    data_run = {"dir": None, "seq_start": 0, "seq_end": 0, "count": 0, "ts": 0}
    unknown_ops = set()

    def flush_data_run():
        if data_run["count"] > 0:
            print(
                f"  {'  -->' if data_run['dir']=='host' else '<--  '}  "
                f"  {data_run['ts']-base:8.3f}s  "
                f"PRINT_IMAGE_DOWNLOAD_DATA  "
                f"seq {data_run['seq_start']}–{data_run['seq_end']}  "
                f"({data_run['count']} chunks)"
            )
            data_run["count"] = 0

    for ts, direction, op1, op2, payload in packets:
        # Collapse DATA chunks
        if op1 == 0x10 and op2 == 0x01:
            seq = struct.unpack_from(">I", payload, 0)[0] if len(payload) >= 4 else 0
            if data_run["count"] == 0:
                data_run.update({"dir": direction, "seq_start": seq, "seq_end": seq,
                                 "count": 1, "ts": ts})
            else:
                data_run["seq_end"] = seq
                data_run["count"]  += 1
            continue

        flush_data_run()

        name, marker = op_label(op1, op2)
        dr = "  -->" if direction == "host" else "<--  "
        detail = payload_detail(op1, op2, payload)
        print(f"  {dr}  {ts-base:8.3f}s  {name}{detail}{marker}")

        if (op1, op2) not in LINK_OPS:
            unknown_ops.add((op1, op2))

    flush_data_run()

    # ── Verbose: every packet ────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("ALL PACKETS (verbose)")
    print("=" * 80)
    prev_op = None
    data_count = 0

    for ts, direction, op1, op2, payload in packets:
        if op1 == 0x10 and op2 == 0x01:
            data_count += 1
            prev_op = (op1, op2)
            continue
        if prev_op == (0x10, 0x01) and data_count:
            print(f"         ... [{data_count} DOWNLOAD_DATA chunks omitted] ...")
            data_count = 0
        prev_op = (op1, op2)

        name, marker = op_label(op1, op2)
        dr = "  -->" if direction == "host" else "<--  "
        print(f"  {dr}  {ts-base:9.3f}s  op=({op1:#04x},{op2:#04x})  {name}")
        if len(payload) <= 32:
            print(f"           payload [{len(payload)}B]: {payload.hex()}")
        else:
            print(f"           payload [{len(payload)}B]: {payload[:32].hex()} ...")
        if marker:
            print(f"           ^^^ {marker.strip()}")

    if data_count:
        print(f"         ... [{data_count} DOWNLOAD_DATA chunks omitted] ...")

    # ── Summary ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    seen_ops = {(op1, op2) for _, _, op1, op2, _ in packets}
    new_ops  = seen_ops - OUR_OPS

    print(f"\nAll opcodes seen ({len(seen_ops)}):")
    for op in sorted(seen_ops):
        name = LINK_OPS.get(op, f"UNKNOWN({op[0]:#04x},{op[1]:#04x})")
        flag = "  ← NOT IN OUR TOOL" if op not in OUR_OPS else ""
        print(f"  op=({op[0]:#04x},{op[1]:#04x})  {name}{flag}")

    if new_ops:
        print(f"\nNEW opcodes to investigate ({len(new_ops)}):")
        for op in sorted(new_ops):
            name = LINK_OPS.get(op, f"UNKNOWN({op[0]:#04x},{op[1]:#04x})")
            relevant = [p for ts, d, o1, o2, p in packets if (o1, o2) == op]
            print(f"\n  op=({op[0]:#04x},{op[1]:#04x})  {name}")
            for i, p in enumerate(relevant[:5]):
                print(f"    [{i}] [{len(p)}B] {p.hex()}")
            if len(relevant) > 5:
                print(f"    ... ({len(relevant)-5} more)")
    else:
        print("\nNo new opcodes found — all opcodes match our implementation.")


if __name__ == "__main__":
    main()
