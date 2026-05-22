# Quickstart — implement an Instax Evo Link client from scratch

← [Wiki index](README.md)

This page is a single, self-contained walkthrough. Reading **only** this page
plus [link-protocol.md](link-protocol.md) (for the opcode table) is enough to
build a working Python client that connects, prints, pulls live-view frames,
and downloads photos from an Instax Evo Wide (FI028) or Mini Evo (FI019).

If you need raw packet excerpts for any step here, use [evidence.md](evidence.md).

Every code block in this file is runnable as-is against a real camera. Required
deps: `pip install bleak pillow`.

## 1. Camera identity & GATT

All Evo cameras advertise the same Instax service. Connect by **address
string** (not by `BLEDevice` — see [implementation.md](implementation.md) for
why), subscribe to notify, and you're ready.

```python
SERVICE_UUID = "70954782-2d83-473d-9e5f-81e1d02d5273"
WRITE_UUID   = "70954783-2d83-473d-9e5f-81e1d02d5273"   # Write + WriteNoResp
NOTIFY_UUID  = "70954784-2d83-473d-9e5f-81e1d02d5273"   # Notify

# Confirmed cameras (BLE address, model ID):
FI019_ADDR = "FA:AB:BC:11:6F:D2"   # Mini Evo  (Gen 1)
FI028_ADDR = "FA:AB:BC:1D:0A:7B"   # Evo Wide  (Gen 2)
```

Filter scans by the service UUID, not by the advertising name. The Mini Evo
advertises as `INSTAX-<serial>(IOS)`; the Evo Wide as `INSTAX-<serial>(BLE)`.
Both speak the same Link protocol.

## 2. Packet framing

Every request/response is one Link frame. Build and validate with:

```python
import struct

HDR_REQ = b"\x41\x62"   # phone → camera ("Ab")
HDR_RSP = b"\x61\x42"   # camera → phone ("aB")

def make_packet(op1: int, op2: int, payload: bytes = b"") -> bytes:
    """Build a Link request packet (phone → camera)."""
    body = HDR_REQ + struct.pack(">H", 7 + len(payload)) + bytes([op1, op2]) + payload
    cs   = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])

def valid_checksum(packet: bytes) -> bool:
    return (sum(packet) & 255) == 255
```

See [link-protocol.md](link-protocol.md) for the full opcode table.

## 3. Frame reassembly helper (used by every flow)

BLE delivers each notification as one ATT packet (≤ MTU − 3 bytes). Link frames
larger than that span multiple notifications. The first notification carries
the `61 42 [len] …` header; the rest are raw continuation bytes with no header.
Reassemble by parsing the length out of the header notification and waiting
until the accumulator holds `length` bytes.

This helper is the foundation of every read in this client. Live view,
auto-transfer, share-pull, queue-transfer, HIST, and register reads all use it.

```python
import asyncio
from bleak import BleakClient

class LinkClient:
    def __init__(self, address: str):
        self.address = address
        self.client: BleakClient | None = None
        self._rx: asyncio.Queue[bytes] = asyncio.Queue()

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._rx.put_nowait(bytes(data))

    async def connect(self) -> None:
        self.client = BleakClient(self.address, timeout=30)
        await self.client.connect()
        await asyncio.sleep(1.0)               # WinRT GATT cache settle
        for attempt in range(3):
            try:
                await self.client.start_notify(NOTIFY_UUID, self._on_notify)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2.0)

    async def disconnect(self) -> None:
        if self.client:
            await self.client.disconnect()
            self.client = None

    async def write(self, packet: bytes) -> None:
        # Link frames larger than ~182 bytes must be split across BLE writes.
        # Keep this conservative chunk size for WinRT stability.
        max_write = 182
        for i in range(0, len(packet), max_write):
            await self.client.write_gatt_char(
                WRITE_UUID,
                packet[i:i + max_write],
                response=False,
            )

    async def flush_rx(self) -> None:
        while not self._rx.empty():
            self._rx.get_nowait()

    async def recv_frame(self, timeout: float = 5.0) -> tuple[int, int, bytes]:
        """Return (op1, op2, payload) for the next complete camera frame."""
        async def _read_one() -> tuple[int, int, bytes]:
            buf = bytearray()
            total: int | None = None
            while True:
                chunk = await self._rx.get()
                buf.extend(chunk)
                if total is None and len(buf) >= 4 and buf[:2] == HDR_RSP:
                    total = struct.unpack_from(">H", buf, 2)[0]
                if total is not None and len(buf) >= total:
                    frame = bytes(buf[:total])
                    # If extra bytes arrived (e.g. a spontaneous frame), keep them.
                    leftover = bytes(buf[total:])
                    if leftover:
                        self._rx.put_nowait(leftover)
                    op1, op2 = frame[4], frame[5]
                    payload  = frame[6:-1]
                    return op1, op2, payload
        return await asyncio.wait_for(_read_one(), timeout=timeout)
```

Notes:
- `payload` is the bytes **between** `op1/op2` and the trailing checksum.
- Treat any byte position offsets in the per-feature pages as offsets into
  this `payload` (i.e. the first byte after `op2`).
- Never use a time-based drain window in place of `recv_frame` — see
  [live-view.md](live-view.md) for why.

## 4. Minimum session init

This is the universal handshake. On FI028 you **must** also send `(0x20,0x10)`
and `(0x80,0x10,[00])` if you want shots taken while connected to land in the
camera-side HIST buffer. See [session-init.md](session-init.md).

```python
async def init_session(c: LinkClient) -> dict:
    """Run the minimum handshake. Returns a dict of identity + capability fields."""
    info: dict = {}

    # 1. Hello
    await c.write(make_packet(0x00, 0x00))
    await c.recv_frame(timeout=3.0)

    # 2. Identity strings (manufacturer / model / serial)
    for itype, key in [(0x00, "manufacturer"), (0x01, "model"), (0x02, "serial")]:
        await c.write(make_packet(0x00, 0x01, bytes([itype])))
        _, _, p = await c.recv_frame(timeout=3.0)
        slen = p[2]
        info[key] = p[3:3 + slen].decode("ascii", errors="replace")

    # 3. Image dimensions (always use these — never hard-code by model)
    await c.write(make_packet(0x00, 0x02, b"\x00"))
    _, _, p = await c.recv_frame(timeout=3.0)
    info["img_w"], info["img_h"] = struct.unpack_from(">HH", p, 2)

    # 4. Battery
    await c.write(make_packet(0x00, 0x02, b"\x01"))
    _, _, p = await c.recv_frame(timeout=3.0)
    info["battery_state"], info["battery_pct"] = p[2], p[3]

    # 5. Photos left in cartridge
    await c.write(make_packet(0x00, 0x02, b"\x02"))
    _, _, p = await c.recv_frame(timeout=3.0)
    info["photos_left"] = p[2] & 0x0F

    # 6. Enable live HIST tracking on Gen 2 (no-op but safe on Gen 1).
    await c.write(make_packet(0x20, 0x10))
    try: await c.recv_frame(timeout=2.0)
    except asyncio.TimeoutError: pass
    await c.write(make_packet(0x80, 0x10, b"\x00"))
    try: await c.recv_frame(timeout=2.0)
    except asyncio.TimeoutError: pass

    return info
```

## 5. Print one image

Works on both FI019 and FI028. The image must already be resized to the camera's
reported `img_w × img_h` and JPEG-encoded under the buffer ceiling. See
[print.md](print.md) for the image-prep details and the size cap.

```python
import math
from io import BytesIO
from pathlib import Path
from PIL import Image

CHUNK = 900   # all Mini/Wide cameras

def prepare_jpeg(path: str, w: int, h: int) -> bytes:
    """Letterbox to w×h and binary-search JPEG quality under ~105 KB scaled."""
    img = Image.open(path).convert("RGB")
    img.thumbnail((w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    max_bytes = int(105 * 1024 * w * h / (600 * 800))
    lo, hi, q = 1, 95, 80
    for _ in range(14):
        buf = BytesIO(); canvas.save(buf, format="JPEG", quality=q)
        size = buf.tell()
        if size <= max_bytes and size >= max_bytes * 0.9:
            return buf.getvalue()
        if size > max_bytes: hi = q - 1
        else:                lo = q + 1
        q = (lo + hi) // 2
    return buf.getvalue()

async def print_image(c: LinkClient, jpeg_path: str, w: int, h: int,
                       eject: bool = True) -> None:
    data = prepare_jpeg(jpeg_path, w, h)
    n    = math.ceil(len(data) / CHUNK)

    # (10,00) START — payload: 02 00 00 00 + size BE
    await c.flush_rx()
    await c.write(make_packet(0x10, 0x00, b"\x02\x00\x00\x00" + struct.pack(">I", len(data))))
    await c.recv_frame(timeout=10.0)

    # (10,01) DATA × N — payload: seq BE + 900 B zero-padded
    for seq in range(n):
        chunk = data[seq * CHUNK:(seq + 1) * CHUNK]
        chunk = chunk + bytes(CHUNK - len(chunk))
        await c.write(make_packet(0x10, 0x01, struct.pack(">I", seq) + chunk))
        await c.recv_frame(timeout=10.0)

    # (10,02) END
    await c.write(make_packet(0x10, 0x02))
    await c.recv_frame(timeout=10.0)

    # (10,80) PRINT — eject film
    if eject:
        await c.write(make_packet(0x10, 0x80))
        await c.recv_frame(timeout=15.0)
```

## 6. Pull one live-view frame

Live view is a phone-driven pull loop. Each `(0x82,01)` returns one complete
small JPEG with a 5-byte prefix. On Gen 1 the first 1–12 pulls may return
short non-JPEG payloads (warm-up); skip those and keep pulling.

```python
async def liveview_one_frame(c: LinkClient) -> bytes:
    """Open a live-view session, return the first valid JPEG, close."""
    await c.flush_rx()
    await c.write(make_packet(0x82, 0x00, b"\x00"))      # open, slot 0
    await c.recv_frame(timeout=3.0)

    try:
        for _ in range(15):                              # warm-up tolerant
            await c.write(make_packet(0x82, 0x01))       # pull
            op1, op2, p = await c.recv_frame(timeout=5.0)
            if op1 == 0x82 and op2 == 0x01 and len(p) > 5:
                soi = p.find(b"\xff\xd8", 5)
                eoi = p.rfind(b"\xff\xd9")
                if soi >= 0 and eoi > soi:
                    return p[soi:eoi + 2]
        raise RuntimeError("no JPEG frame received")
    finally:
        await c.write(make_packet(0x82, 0x02, b"\x00"))  # close
        try: await c.recv_frame(timeout=2.0)
        except asyncio.TimeoutError: pass
```

For continuous live view and seamless post-shutter download see
[live-view.md](live-view.md) and [auto-transfer.md](auto-transfer.md).

## 7. Pull one share-button photo (FI028 only)

When the user presses **Share** on a Gen 2 camera, send the `(0x85,xx)` prepare
sequence; ~700 ms later `CAMERA_FUNCTION_INFO[4]` flips non-zero; then run
`(0x88,xx)`. **Do not send `(0x88,xx)` to a Mini Evo (FI019)** — it disconnects.

```python
async def share_pull_one(c: LinkClient, timeout_s: float = 30.0) -> bytes:
    # 1. Prepare
    await c.write(make_packet(0x85, 0x00))
    await c.recv_frame(timeout=3.0)
    await c.write(make_packet(0x85, 0x01, bytes.fromhex("05" + "00" * 8)))
    await c.recv_frame(timeout=3.0)
    await c.write(make_packet(0x85, 0x00))
    await c.recv_frame(timeout=3.0)

    # 2. Poll CAMERA_FUNCTION_INFO until byte[4] of payload-after-InfoType is non-zero.
    #    Payload layout: [0x00][InfoType=0x04][data…] → data[2] = transfer flag.
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        await c.write(make_packet(0x00, 0x02, b"\x04"))
        _, _, p = await c.recv_frame(timeout=3.0)
        if len(p) >= 5 and p[4] != 0x00:
            break
        await asyncio.sleep(0.5)
    else:
        raise TimeoutError("camera never raised transfer-ready flag")

    # 3. Start pull
    await c.write(make_packet(0x88, 0x00))
    _, _, ack = await c.recv_frame(timeout=5.0)
    if ack[:5] != b"\x00\x00\x00\x00\x00":
        raise RuntimeError(f"camera NACK: {ack.hex()}")

    # 4. Metadata: total_size, chunk_size
    await c.write(make_packet(0x88, 0x01, b"\x00\x00\x00\x00"))
    _, _, meta = await c.recv_frame(timeout=5.0)
    total_size = struct.unpack_from(">I", meta, 1)[0]
    chunk_size = struct.unpack_from(">I", meta, 5)[0]

    # 5. Drain N chunks
    n_chunks = math.ceil(total_size / chunk_size)
    jpeg = bytearray()
    for idx in range(n_chunks):
        await c.write(make_packet(0x88, 0x02, struct.pack(">I", idx)))
        _, _, cp = await c.recv_frame(timeout=10.0)
        jpeg.extend(cp[5:])   # skip [img_idx:4][chunk_seq:1]

    # 6. Close
    await c.write(make_packet(0x88, 0x03))
    await c.recv_frame(timeout=3.0)
    await c.write(make_packet(0x88, 0x05, b"\x00\x00\x00\x00"))
    await c.recv_frame(timeout=3.0)
    return bytes(jpeg)
```

## 8. Pull one post-shutter photo via `(0x82,10/20/21/22)`

This is the camera-side equivalent of the share-button pull. It works on FI028
(after a spontaneous `(0x82,0x02)` during live view) and on FI019 (after the
app-style "stop live view then download" sequence). Do **not** invoke it on
FI019 while live view is still open — the camera replies `[0xc0]` and no image
becomes ready.

```python
async def receive_82(c: LinkClient) -> bytes | None:
    # 1. Query — begin receive path
    await c.write(make_packet(0x82, 0x10, b"\x00"))
    o1, o2, _ = await c.recv_frame(timeout=3.0)
    if not (o1 == 0x82 and o2 == 0x10):
        return None

    # 2. Poll until ready (~4–5 s)
    for _ in range(60):
        await c.write(make_packet(0x82, 0x20))
        o1, o2, p = await c.recv_frame(timeout=2.0)
        if o1 == 0x82 and o2 == 0x20 and len(p) >= 10:
            total_size = struct.unpack_from(">I", p, 2)[0]
            chunk_size = struct.unpack_from(">I", p, 6)[0]
            break
        await asyncio.sleep(0.5)
    else:
        return None

    # 3. Request each chunk
    n_chunks = math.ceil(total_size / chunk_size)
    jpeg = bytearray()
    for idx in range(n_chunks):
        await c.write(make_packet(0x82, 0x21, struct.pack(">I", idx)))
        o1, o2, cp = await c.recv_frame(timeout=10.0)
        if not (o1 == 0x82 and o2 == 0x21):
            break
        jpeg.extend(cp[5:])   # skip [status:1][chunk_idx:4]

    # 4. Close
    await c.write(make_packet(0x82, 0x22))
    try: await c.recv_frame(timeout=2.0)
    except asyncio.TimeoutError: pass
    return bytes(jpeg) if len(jpeg) > 100 else None
```

## 9. Putting it together — a complete runnable example

```python
import asyncio

async def main():
    c = LinkClient(FI028_ADDR)
    await c.connect()
    try:
        info = await init_session(c)
        print(info)
        # Example: print one image:
        await print_image(c, "photo.jpg", info["img_w"], info["img_h"], eject=False)
        # Example: grab one live-view JPEG:
        jpeg = await liveview_one_frame(c)
        with open("frame.jpg", "wb") as f: f.write(jpeg)
    finally:
        await c.disconnect()

asyncio.run(main())
```

## 10. Decision matrix — which flow do I use?

| You want to … | Use opcode family | Page |
|---|---|---|
| Read battery / photos left / model | `(0x00,0x01/0x02)` | [session-init.md](session-init.md) |
| Send an image and eject it | `(0x10,xx)` | [print.md](print.md) |
| Show a viewfinder stream | `(0x82,00/01/02)` | [live-view.md](live-view.md) |
| Download a photo the camera just shot | `(0x82,10/20/21/22)` | [auto-transfer.md](auto-transfer.md) |
| Drain the share-button queue (FI028 only) | `(0x85,xx)` + `(0x88,xx)` | [image-pull.md](image-pull.md) |
| Bulk-download all queued prints (QUE button) | `(0x84,xx)` + `(0x80,15)` + `(0x82,xx)` | [queue-transfer.md](queue-transfer.md) |
| Change flash / read film/lens effect (FI028) | `(0x80,11)` | [registers.md](registers.md) |
| Read per-film/per-lens shot tallies | `(0x84,00/01/02/09/0a/0b)` | [history-log.md](history-log.md) |
| Watch the live shot counter while idle | `(0x00,0x02,[0x05])` | [history-log.md § Runtime polling](history-log.md#runtime-polling-for-live-counters) |

Mutually-exclusive rule of thumb: live view, share-pull, queue-transfer, and
print all share the same notify channel. **Never run two at once.** Drive them
from a single task with a `_ble_busy` flag.

## 11. Common pitfalls

- **Construct `BleakClient` from an address string**, not from a `BLEDevice` —
  the latter inherits a stale GATT cache on Windows after a disconnect.
- **Do not call `client.pair()` on every session.** Once the OS-level bond
  exists, WinRT re-uses it. Calling `pair()` mid-session on an already-bonded
  Wide Evo causes the camera to drop the link.
- **MTU 23 at connect = unbonded.** Disconnect, ensure the Windows pairing
  exists, reconnect.
- **Never use a time-based drain window** in place of `recv_frame`. Multi-
  notification frames (live view, share-pull chunks) can arrive partially.
- **Do not retry `(0x88,xx)` on FI019.** The camera disconnects on the first
  attempt and will keep doing so. Use the `(0x82,10/20/21/22)` flow instead.
- **`(0x84,0x0b) HIST_DONE` clears the camera-side tally buffer.** If you
  send it without first reading the buffer, you lose those counters forever.
  Maintain running totals phone-side.
- **Sequence counter is global across BLE sessions.** A reconnect does not
  reset any camera-side state — keep your own counters, do not assume zero.

## 12. Where to read next

For implementation depth on any single flow, jump straight to its page from
the table above. For platform-specific gotchas (WinRT, bleak, pairing), see
[implementation.md](implementation.md). For unresolved areas (remote shutter
on Gen 2, FI019 flash writes), see [roadmap.md](roadmap.md).
