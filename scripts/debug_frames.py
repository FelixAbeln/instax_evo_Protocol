"""Debug: find 0x61 0x42 IOS-Link camera→phone frames in btsnoop."""
import struct

LOG = r'c:\Users\Compf\Downloads\Phone Link\bugreport_0518b\FS\data\log\bt\btsnoop_hci.log'


def iter_packets(path):
    with open(path, 'rb') as f:
        magic = f.read(8)
        assert magic == b'btsnoop\x00', f"bad magic: {magic!r}"
        f.read(8)  # version + datalink type
        while True:
            hdr = f.read(24)
            if len(hdr) < 24:
                break
            orig_len, inc_len, flags, drops, ts = struct.unpack('>IIIIQ', hdr)
            data = f.read(inc_len)
            if len(data) < inc_len:
                break
            yield flags, data


count = 0
for flags, pkt in iter_packets(LOG):
    if len(pkt) < 5:
        continue
    if pkt[0] != 0x02:  # not ACL
        continue
    acl = pkt[1:]
    raw = bytes(acl)
    pos = raw.find(b'\x61\x42')
    if pos >= 0:
        count += 1
        if count <= 8:
            print(f"flags={flags} pkt_len={len(pkt)} acl_len={len(acl)}")
            hdr_hex = ' '.join(f'{b:02x}' for b in raw[:8])
            seg_hex = ' '.join(f'{b:02x}' for b in raw[pos:pos+20])
            print(f"  ACL hdr  : {hdr_hex}")
            print(f"  @[{pos}]: {seg_hex}")
            print()

print(f"Total ACL packets containing 61 42: {count}")
