"""Scan new 19-51-52 capture — both btsnoop files."""
import struct
from pathlib import Path

BASE = Path("captures/extracted/19-51-52/FS/data/log/bt")


def scan(path):
    handles = {}
    conns = []
    n_total = 0
    with open(path, "rb") as f:
        f.read(16)  # file header
        while True:
            rec = f.read(24)
            if len(rec) < 24:
                break
            orig_len, inc_len, flags, drops = struct.unpack(">IIII", rec[:16])
            ts_us = struct.unpack(">q", rec[16:])[0]
            ts_sec = (ts_us - 0x00E03AB44A676000) / 1_000_000
            data = f.read(inc_len)
            if not data:
                continue
            n_total += 1

            # LE Connection Complete
            if data[0] == 0x04 and len(data) >= 5:
                if data[1] == 0x3E and data[3] == 0x01 and len(data) >= 15:
                    addr = ":".join(f"{b:02X}" for b in reversed(data[9:15]))
                    conns.append((ts_sec, addr))

            # ATT Write Command (0x52) or Write Request (0x12)
            if data[0] != 0x02 or len(data) < 12:
                continue
            cid = struct.unpack_from("<H", data, 7)[0]
            if cid != 0x0004:
                continue
            att_op = data[9]
            if att_op not in (0x52, 0x12) or len(data) < 12:
                continue
            h = struct.unpack_from("<H", data, 10)[0]
            v = data[12:]
            if h not in handles:
                handles[h] = {"count": 0, "first_ts": ts_sec, "last_ts": ts_sec, "samples": []}
            handles[h]["count"] += 1
            handles[h]["last_ts"] = ts_sec
            if len(handles[h]["samples"]) < 3:
                handles[h]["samples"].append(v[:20].hex())

    return handles, conns, n_total


for name in ["btsnoop_hci.log.last", "btsnoop_hci.log"]:
    p = BASE / name
    handles, conns, n_total = scan(p)
    print(f"\n{'='*60}")
    print(f"  {name}  ({p.stat().st_size // 1024} KB,  {n_total} records)")
    print(f"{'='*60}")
    print(f"LE connections: {len(conns)}")
    for ts, addr in conns:
        print(f"  t={ts:8.1f}s  {addr}")
    print(f"\nWrite handles ({len(handles)}):")
    for h, info in sorted(handles.items()):
        span = info["last_ts"] - info["first_ts"]
        print(
            f"  h=0x{h:04X}  n={info['count']:6d}"
            f"  t={info['first_ts']:7.1f}s→{info['last_ts']:7.1f}s  (+{span:.0f}s)"
            f"  eg={info['samples'][0][:32]}"
        )
