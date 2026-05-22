import struct
from pathlib import Path
p=Path(r'f:/instax_evo_Protocol/captures/bugreport_2026-05-20/FS_data_log_bt_btsnoop_hci.log.last')
rows=[]
with p.open('rb') as f:
    f.read(16)
    t0=None
    while True:
        rec=f.read(24)
        if len(rec)<24: break
        o,i,fl,dr=struct.unpack('>IIII',rec[:16])
        ts=struct.unpack('>q',rec[16:])[0]
        s=(ts-0x00E03AB44A676000)/1_000_000
        if t0 is None: t0=s
        data=f.read(i)
        if not data or data[0]!=0x02 or len(data)<12: continue
        cid=struct.unpack_from('<H',data,7)[0]
        if cid!=0x0004: continue
        op=data[9]; h=struct.unpack_from('<H',data,10)[0]; v=data[12:]
        t=s-t0
        if 3542.0 <= t <= 3552.0 and h in (0x0020,0x001d):
            dir='W' if op in (0x12,0x52) else ('N' if op in (0x1b,0x1d) else '?')
            rows.append((t,dir,h,v.hex(),len(v)))
for t,d,h,x,l in rows:
    print(f'{t:8.3f} {d} h=0x{h:04x} len={l:3d} {x}')
