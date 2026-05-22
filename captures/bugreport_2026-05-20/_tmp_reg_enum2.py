import struct
from pathlib import Path
from collections import Counter

p=Path(r'f:/instax_evo_Protocol/captures/bugreport_2026-05-20/FS_data_log_bt_btsnoop_hci.log.last')
rows=[]
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
        if 3542.0 <= t <= 3552.0 and h==0x0020 and op in (0x12,0x52):
            rows.append((t,bytes(v)))

print('candidates len>=10 and v[5]==0x05')
pat=[]
for t,v in rows:
    if len(v)>=10 and v[5]==0x05:
        pat.append((t,v))
        print(f't={t:8.3f} len={len(v):2d} tx={v[0:2].hex()} kind={v[2:4].hex()} reg={v[4]:02x} marker={v[5]:02x} rw={v[6]:02x} val={v[7]:02x} raw={v.hex()}')

print('\nGrouped by (kind,reg,rw,val):')
c=Counter((v[2:4].hex(),v[4],v[6],v[7]) for _,v in pat)
for (kind,reg,rw,val),n in sorted(c.items()):
    print(f'  kind={kind} reg=0x{reg:02x} rw=0x{rw:02x} val=0x{val:02x} x{n}')
