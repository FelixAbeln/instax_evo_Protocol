import struct
from collections import defaultdict
from pathlib import Path

p = Path(r"f:/instax_evo_Protocol/captures/bugreport_2026-05-20/FS_data_log_bt_btsnoop_hci.log.last")
rows = []

with p.open("rb") as f:
    f.read(16)
    t0 = None
    while True:
        rec = f.read(24)
        if len(rec) < 24:
            break
        _o, inc_len, _fl, _dr = struct.unpack(">IIII", rec[:16])
        ts = struct.unpack(">q", rec[16:])[0]
        s = (ts - 0x00E03AB44A676000) / 1_000_000
        if t0 is None:
            t0 = s
        data = f.read(inc_len)
        if not data or data[0] != 0x02 or len(data) < 12:
            continue
        cid = struct.unpack_from("<H", data, 7)[0]
        if cid != 0x0004:
            continue
        op = data[9]
        h = struct.unpack_from("<H", data, 10)[0]
        v = data[12:]
        t = s - t0
        if 3542.0 <= t <= 3552.0 and h == 0x0020 and op in (0x12, 0x52):
            rows.append((t, bytes(v)))

print(f"writes in window: {len(rows)}")

families = defaultdict(list)
for t, v in rows:
    if len(v) < 6:
        continue
    body = v[2:]
    key = body[:4]
    families[key].append((t, v, body))

print("\nTop families by body[:4]:")
for k, items in sorted(families.items(), key=lambda kv: -len(kv[1]))[:30]:
    print(f"  key={k.hex()} count={len(items)} sample={items[0][1].hex()}")


def diff_positions(a: bytes, b: bytes):
    n = min(len(a), len(b))
    pos = [i for i in range(n) if a[i] != b[i]]
    if len(a) != len(b):
        pos.extend(range(n, max(len(a), len(b))))
    return pos

print("\nNear-identical pairs (<=2 body-byte diffs; ignores tx-id):")
found = 0
for i in range(len(rows)):
    t1, v1 = rows[i]
    b1 = v1[2:]
    for j in range(i + 1, min(i + 50, len(rows))):
        t2, v2 = rows[j]
        if len(v1) != len(v2) or len(v1) < 10:
            continue
        b2 = v2[2:]
        pos = diff_positions(b1, b2)
        if 1 <= len(pos) <= 2:
            print(f"  t={t1:.3f}->{t2:.3f} len={len(v1)} diff={pos}\\n    {v1.hex()}\\n    {v2.hex()}")
            found += 1
            if found >= 40:
                break
    if found >= 40:
        break

print("\nPackets containing 0501/0502 motif:")
for t, v in rows:
    hx = v.hex()
    if "0501" in hx or "0502" in hx:
        print(f"  t={t:.3f} len={len(v)} {hx}")
