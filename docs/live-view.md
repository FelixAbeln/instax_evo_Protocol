# Live view — `(0x82,xx)` pull protocol

← [Wiki index](README.md)

The `(0x82,xx)` opcodes are used for two related but distinct operations:

| Context | What's sent | Each `(0x82,01)` returns |
|---|---|---|
| **Live view** | Slot 0, no preceding `(0x84,xx)` | One complete small JPEG (~1 KB) of current camera view |
| **History download** | Slot = history index, preceded by `(0x84,09)/(0x84,0a)/(0x84,0b)` | One chunk of a large stored JPEG |

Both use identical on-wire framing. The preceding `(0x84,xx)` setup (or lack
thereof) tells the camera which mode to use. **Do not close and reopen the
session between pulls** — keep one `(0x82,00)` session open for the entire
image/sequence.

## Confirmed session timing (Mini Evo session 156)

From a decoded Windows HCI log (`captures/handle_split.txt`, UTF-16 LE):

```
t=125.73 s   phone → cam: op=(0x80,0x15)  payload=[17×0x00]   LIVE_VIEW_PREPARE
t=125.82 s   phone → cam: op=(0x82,0x00)  payload=[0x00]      open session, slot=0
t=125.82 s   cam → phone: op=(0x82,0x00)  [0x00]              ACK (echo slot)
t=125.87 s   phone → cam: op=(0x82,0x01)  []                  first pull
t=125.87 s   cam → phone: op=(0x82,0x01)  [2B][3B][JPEG…]    first response
…            175 × (0x82,01) at ~50 ms intervals …
t=134.38 s   phone → cam: op=(0x82,0x02)  payload=[0x00]      close session (sent TWICE)
t=134.38 s   cam → phone: op=(0x82,0x02)  [0x00]              ACK

Total session:  8.56 s
Pull count:     175 × (0x82,01) @ ~50 ms cadence
Cam→phone:      1600 packets, 199,990 bytes, 176 JPEG sigs
```

Key observations:
- **One JPEG per pull**: 175 pulls → 176 JPEG SOI markers means each `(0x82,01)`
  response carries one complete small JPEG (~1136 bytes average at 160×106 px,
  low quality).
- **Session stays open**: `(0x82,00)` is sent once; `(0x82,02)` is sent only
  when the app is done — **not** between frames. Opening a new session per
  frame would add ~8.5 s overhead.
- **`(0x82,02)` sent twice**: the real app sends the end command twice in
  succession; the camera responds to both. Sending twice is safe.
- **Pull cadence**: 50 ms between sends (20 fps). Implement with a ~50 ms
  notification drain window after each pull; total per-frame latency ≈ 50–100 ms.

## Full live view sequence

```
phone → cam: op=(0x80,0x15)  payload=[17×0x00]   prepare
cam → phone: op=(0x80,0x15)  [response]           ACK (Mini Evo: 1B 0xBF; Wide: 17B)

# Flush any stale notifications from _rx before opening
phone → cam: op=(0x82,0x00)  payload=[0x00]       open session (slot 0)
cam → phone: op=(0x82,0x00)  [0x00]               ACK

loop until user stops or camera sends (0x82,02):
    phone → cam: op=(0x82,0x01)  payload=[]                           pull request
    cam → phone: op=(0x82,0x01)  [2B chunk_idx][3B header][JPEG…]    response
        # Use _recv_frame() — accumulates 5 ATT notifications into 1027-byte frame
        # JPEG data starts at payload[5] — skip 2B chunk_idx + 3B header field
        # After emitting frame: drain _rx for a spontaneous (0x82,02) without blocking

    if (0x82,02) received (shutter fired, frame_count > 0):
        # Acknowledge, run the chunk transfer inline, then reopen — seamlessly.
        phone → cam: op=(0x82,0x02)  payload=[0x00]   # ack the close
        << run IMG_HIST_QUERY / IMG_HIST_POLL / chunk loop / IMG_HIST_END >>
        sleep(2.0)                                    # camera recovery time
        phone → cam: op=(0x82,0x00)  payload=[0x00]   # reopen pull session
        cam → phone: op=(0x82,0x00)  [0x00]           # ACK
        # reset frame_count → 0 and continue pulling
```

> **Inline transfer after shutter (confirmed 2026-05-17):** When the camera
> fires the shutter during live view it sends a spontaneous `(0x82,02)` close.
> Instead of exiting the session management loop entirely, the correct approach
> is to handle the transfer *inside* the inner pull loop: ack the close → run
> the `(0x82,10/20/21/22)` transfer → sleep ~2 s for camera recovery → reopen
> with `(0x82,00)` → continue pulling frames. The user sees no
> "session stopped / starting" interruption.

## `(0x82,01)` response layout

Each `(0x82,01)` response is one Link frame spanning **5 BLE ATT notifications**
(confirmed from btsnoop, bonded MTU = 247 → 244 bytes usable per notification):

```
Notification 1 (244 B):  61 42 [04 03]  82 01  [payload bytes 0–237]   ← Link header + start of payload
Notification 2 (244 B):  [payload bytes 238–481]                        ← raw continuation
Notification 3 (244 B):  [payload bytes 482–725]                        ← raw continuation
Notification 4 (244 B):  [payload bytes 726–969]                        ← raw continuation
Notification 5 ( 51 B):  [payload bytes 970–1019]  [cs]                 ← tail + checksum

Total Link frame: 1027 bytes  (len field = 0x0403)
Payload (frame[6:1026]): 1020 bytes
  payload[0:2]  = chunk index, always 0x00 0x01
  payload[2:5]  = 3-byte frame header field (e.g. 0x00 0x03 0xF7 — varies per frame)
  payload[5:]   = complete JPEG image (SOI 0xFF 0xD8 … EOI 0xFF 0xD9)
                  typical size ~1000 bytes at 160×106 px, low quality
```

Confirmed from btsnoop decode (`captures/handle_split.txt`, session 156 — Mini
Evo): `payload[0:8]` = `00 01  00 03 F7  FF D8 FF` — chunk idx, header, SOI.

## Reassembling the frame in bleak

In bleak all ATT notifications arrive via the same notify callback — both the
Link-framed first notification and the 4 raw continuation bytes. Use
`_recv_frame()` (which accumulates `_rx` bytes into `buf` until `len(buf) >=
total`) rather than a time-based drain window:

```python
op1, op2, payload = await _recv_frame(timeout=5.0)
# payload[5:] is the complete JPEG
soi = payload.find(b'\xff\xd8', 5)
eoi = payload.rfind(b'\xff\xd9')
if soi >= 0 and eoi > soi:
    frame = payload[soi:eoi + 2]
```

**Do not** use a 50 ms time-based drain window — it is unreliable: it may fire
before all 5 notifications arrive (giving a truncated JPEG) or a spontaneous
`(0x82,02)` may arrive after the window closes and corrupt the next pull's
`_recv_frame` call.
