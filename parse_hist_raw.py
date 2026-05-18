"""
Search for 84 0a (HIST_GET_DATA response) frames directly in the raw btsnoop binary.
Then dump the shot records without any ATT-layer reassembly.
"""
import struct

LOG = r'c:\Users\Compf\Downloads\Phone Link\bugreport_0518b\FS\data\log\bt\btsnoop_hci.log'

# Read the whole file as bytes
with open(LOG, 'rb') as f:
    raw = f.read()

# Search for IOS-Link camera→phone HIST_GET_DATA response: 61 42 XX XX 84 0a
marker = b'\x61\x42'
positions = []
start = 0
while True:
    pos = raw.find(marker, start)
    if pos < 0:
        break
    if pos + 6 <= len(raw) and raw[pos+4] == 0x84 and raw[pos+5] == 0x0a:
        total = struct.unpack_from('>H', raw, pos+2)[0]
        positions.append((pos, total))
    start = pos + 1

print(f"Found {len(positions)} HIST_GET_DATA (84,0a) frames at:")
for pos, total in positions:
    print(f"  offset=0x{pos:08x}  total={total} bytes")
print()

REC_SIZE = 44

for i, (pos, total) in enumerate(positions):
    if pos + total > len(raw):
        print(f"Frame {i} at 0x{pos:08x}: truncated (have {len(raw)-pos}, need {total})")
        continue
    frame = raw[pos:pos+total]
    # op1=frame[4]=0x84, op2=frame[5]=0x0a
    payload = frame[6:total-1]
    print(f"=== HIST_GET_DATA frame {i}  offset=0x{pos:08x}  payload={len(payload)}B ===")
    print(f"  Header raw: {payload[:16].hex(' ')}")

    # Parse header: 8 bytes then records
    date_int = struct.unpack_from('>I', payload, 0)[0]
    shots   = struct.unpack_from('>H', payload, 4)[0]
    prints  = struct.unpack_from('>H', payload, 6)[0]
    print(f"  date={date_int}  shots={shots}  prints={prints}")

    rec_data = payload[8:]
    n_recs = len(rec_data) // REC_SIZE
    print(f"  {n_recs} records")

    for r in range(n_recs):
        rec = rec_data[r*REC_SIZE:(r+1)*REC_SIZE]
        nz = [(b, hex(rec[b])) for b in range(len(rec)) if rec[b] != 0]
        if nz:
            hex_str = ' '.join(f'{x:02x}' for x in rec)
            print(f"  rec[{r:2d}]: {hex_str}")
            print(f"          non-zero: {nz}")
    print()
