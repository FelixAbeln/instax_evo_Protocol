import struct
from pathlib import Path
p=Path(r'f:/instax_evo_Protocol/captures/bugreport_2026-05-20/FS_data_log_bt_btsnoop_hci.log.last')
count=0
with p.open('rb') as f:
    f.read(16)
    t0=None
    while True:
        rec=f.read(24)
        if len(rec)<24: break
        _o,i,_fl,_dr=struct.unpack('>IIII',rec[:16])
        ts=struct.unpack('>q',rec[16:])[0]
        s=(ts-0x00E03AB44A676000)/1_000_000
        if t0 is None: t0=s
        d=f.read(i)
        if not d or d[0]!=0x02 or len(d)<12: continue
        cid=struct.unpack_from('<H',d,7)[0]
        if cid!=0x0004: continue
        op=d[9]; h=struct.unpack_from('<H',d,10)[0]; v=d[12:]
        t=s-t0
        if 3547.72 <= t <= 3548.05 and h in (0x0020,0x001d):
            dch='W' if op in (0x12,0x52) else ('N' if op in (0x1b,0x1d) else '?')
            print(f'{t:8.3f} {dch} h=0x{h:04x} len={len(v):2d} {v.hex()}')
            count += 1
            if count >= 80:
                break
print('count',count)
