# Auto-transfer — `(0x82,10/20/21/22)`

← [Wiki index](README.md)

When the user takes a photo via remote shutter (phone app shutter button during
live view), the camera automatically encodes the JPEG and makes it available
for transfer via the `(0x82,0x10/0x20/0x21/0x22)` opcode family.

This is **distinct from the `(0x88,xx)` share-button pull** described in
[image-pull.md](image-pull.md). Both protocols use phone-initiated chunk
requests (the camera does NOT push).

Confirmed on Gen 2 (Evo Wide FI028), bugreport 0517b 2026-05-17: three photos
transferred at sizes 216 035 B, 216 968 B, 213 221 B; each took ~22–23 chunks
at 9 749 B/chunk.

## Transfer sequence

```
# ── Trigger (immediately after LIVE_VIEW_END) ────────────────────────────
phone → cam: op=(0x82,0x10)  payload=[0x00]     # IMG_HIST_QUERY
cam → phone: op=(0x82,0x10)  payload=[0x00]     # ACK

# ── Poll loop (~500 ms interval) ─────────────────────────────────────────
loop:
  phone → cam: op=(0x82,0x20)  payload=[]        # IMG_HIST_POLL — is image ready?
  cam → phone: op=(0x82,0x20)  payload=[0x02]    # not ready — retry
  ... (camera takes ~4–5 s to encode the JPEG) ...
  cam → phone: op=(0x82,0x20)  payload=[0x00][0x02][total_size:4B BE][chunk_size:4B BE]
               # READY — total_size and chunk_size (bytes per chunk excl. header)
               # Example: 00 02 00034be3 00002615 → total=215011 B, chunk=9749 B

# ── Chunk transfer (REQUEST-RESPONSE — confirmed from btsnoop 0517b) ─────
# The phone requests each chunk; the camera responds. The next request is the
# implicit ACK for the previous chunk. No separate ACK frame is ever sent.
num_chunks = ceil(total_size / chunk_size)
for chunk_idx in range(num_chunks):
  phone → cam: op=(0x82,0x21)  payload=[chunk_idx:4B BE]                    # REQUEST
  cam → phone: op=(0x82,0x21)  payload=[status:1B][chunk_idx:4B BE][jpeg…]  # RESPONSE
  # status byte is always 0x00 (OK)
  # Timing: ~188 ms round-trip per chunk at MTU=247

# ── Close ────────────────────────────────────────────────────────────────
phone → cam: op=(0x82,0x22)  payload=[]     # IMG_HIST_END
cam → phone: op=(0x82,0x22)  payload=[0x00] # ACK
```

## Timing (from bugreport 0517b)

| Event | Offset |
|---|---|
| `LIVE_VIEW_END` (last frame) | T+0 |
| `IMG_HIST_QUERY` (0x82,0x10) | T+0 ms |
| First `IMG_HIST_POLL` not-ready | T+80 ms |
| Last `IMG_HIST_POLL` not-ready | T+4 550 ms |
| `IMG_HIST_POLL` READY | T+4 600 ms |
| First chunk pushed | T+4 796 ms |
| Last chunk pushed / `IMG_HIST_END` | T+9 200 ms |

Total per-image time (encode + transfer): ~9 seconds for a 216 KB JPEG.

## Python receive skeleton

```python
import struct, asyncio

async def receive_82_transfer(backend):
    """Receive an auto-transferred image via the 0x82 history protocol."""
    # 1. Query
    await backend._write(make_packet(0x82, 0x10, b"\x00"))
    o1, o2, _ = await backend._recv_frame(timeout=3.0)
    if not (o1 == 0x82 and o2 == 0x10):
        return None

    # 2. Poll until ready (max ~30 s)
    for _ in range(60):
        await backend._write(make_packet(0x82, 0x20))
        o1, o2, p = await backend._recv_frame(timeout=2.0)
        if o1 == 0x82 and o2 == 0x20 and len(p) >= 10:
            total_size = struct.unpack_from(">I", p, 2)[0]
            chunk_size = struct.unpack_from(">I", p, 6)[0]
            break
        await asyncio.sleep(0.5)
    else:
        return None  # timed out

    # 3. Request each chunk (phone requests, camera responds)
    jpeg = bytearray()
    num_chunks = -(-total_size // chunk_size)
    for chunk_idx in range(num_chunks):
        await backend._write(make_packet(0x82, 0x21, struct.pack(">I", chunk_idx)))
        o1, o2, cp = await backend._recv_frame(timeout=10.0)
        if not (o1 == 0x82 and o2 == 0x21):
            break
        jpeg.extend(cp[5:])  # skip [1B status][4B chunk_idx]

    # 4. Close
    await backend._write(make_packet(0x82, 0x22))
    try:
        await backend._recv_frame(timeout=2.0)
    except asyncio.TimeoutError:
        pass

    return bytes(jpeg) if len(jpeg) > 100 else None
```

## When to trigger

Triggered by a spontaneous `(0x82,02)` close from the camera during live view
(shutter fired). Send `IMG_HIST_QUERY` **immediately after acknowledging the
`(0x82,02)` close** and before reopening the live view session with
`(0x82,00)`. The camera takes ~4–5 s to encode the JPEG after the shutter
fires, so poll `(0x82,20)` at ~500 ms intervals. If the camera has no image
ready it keeps returning `[0x02]` — use a timeout (e.g. 30 s / 60 polls) to
give up gracefully.

This protocol was only observed after **remote shutter captures**. Whether it
is triggered by the Share button (like `(0x88,xx)`) is not yet confirmed.
