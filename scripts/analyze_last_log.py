"""Quick analysis of new btsnoop .last.log - find session starts and handshakes"""
import struct
from pathlib import Path

LOG = Path("captures/extracted/bugreport-pa3qxeea-BP2A.250605.031.A3-2026-05-16-18-40-26__btsnoop_hci.log.last.log")

OP_NAMES = {
    0x52: "WriteNoResp", 0x1B: "Notify", 0x12: "WriteReq", 0x13: "WriteResp",
    0x02: "MTUReq", 0x03: "MTUResp", 0x10: "RdGrpReq", 0x11: "RdGrpResp",
    0x08: "RdTypeReq", 0x09: "RdTypeResp", 0x04: "FindInfoReq", 0x05: "FindInfoResp",
    0x0A: "ReadReq", 0x0B: "ReadResp", 0x01: "Error", 0x1D: "Indicate",
    0x1E: "HandleValueConf",
}


def parse_btsnoop(path):
    with open(path, "rb") as f:
        hdr = f.read(16)
        assert hdr[:8] == b"btsnoop\x00"
        while True:
            rec_hdr = f.read(24)
            if len(rec_hdr) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec_hdr[:16])
            ts_usec = struct.unpack(">q", rec_hdr[16:])[0]
            ts_sec = (ts_usec - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            if not data or data[0] != 0x02 or len(data) < 10:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            direction = "dev" if flags & 1 else "host"
            op = data[9]
            h = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
            p = data[12:] if len(data) > 12 else b""
            yield ts_sec, direction, op, h, p


pkts = list(parse_btsnoop(LOG))
if not pkts:
    print("No ATT packets")
    exit()

base = pkts[0][0]
print(f"ATT packets: {len(pkts)}  Duration: {pkts[-1][0]-base:.1f}s")

# MTU exchanges
print("\nMTU exchanges (= new connections):")
for ts, d, op, h, p in pkts:
    if op in (0x02, 0x03):
        val = struct.unpack_from("<H", p, 0)[0] if len(p) >= 2 else 0
        label = "MTU Request" if op == 0x02 else "MTU Response"
        print(f"  T+{ts-base:8.3f}  {d:4}  {label}  val={val}")

# All write handles
write_h = sorted(set(h for _, d, op, h, _ in pkts if op in (0x52, 0x12) and d == "host"))
notify_h = sorted(set(h for _, d, op, h, _ in pkts if op == 0x1B and d == "dev"))
print(f"\nWrite handles: {[f'0x{h:04X}' for h in write_h]}")
print(f"Notify handles: {[f'0x{h:04X}' for h in notify_h]}")

print("\nAll ATT packets:")
for ts, d, op, h, p in pkts:
    op_n = OP_NAMES.get(op, f"0x{op:02X}")
    dr = "  -->" if d == "host" else "<--  "
    print(f"  T+{ts-base:8.3f}  {dr}  {op_n:13s}  h=0x{h:04X}  [{len(p):3}] {p.hex()[:64]}")
