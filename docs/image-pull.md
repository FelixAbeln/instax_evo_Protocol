# Image pull — `(0x85,xx)` + `(0x88,xx)`

← [Wiki index](README.md)

The phone-initiated, share-button-triggered pull protocol. Used to drain every
photo printed by the camera into the phone's gallery.

This is **distinct from** the `(0x82,10/20/21/22)` post-shutter auto-transfer
(see [auto-transfer.md](auto-transfer.md)) — that path is for shots taken via
the phone's remote shutter during live view; `(0x88,xx)` is for share-button
pulls of already-printed photos.

Confirmed on Gen 2 (Evo Wide FI028) from btsnoop captures 2026-05-17/18 and
live Python/bleak runs.

## DOWNLOAD_PREPARE — `(0x85,xx)`

Before the camera will enter transfer mode, the phone sends a download-prepare
sequence. This causes the camera to raise its `CAMERA_FUNCTION_INFO`
transfer-ready flag ~700 ms later, at which point the poll loop fires
`(0x88,0x00)` to begin the pull.

The camera does NOT raise the flag on its own without this step.

| op1 | op2 | Name | Direction | Payload |
|-----|-----|------|-----------|---------|
| 0x85 | 0x00 | `DOWNLOAD_STATE_QUERY` | P→C (empty); C→P 5B | `[00 00 ff 00 00]` when images are queued |
| 0x85 | 0x01 | `DOWNLOAD_INITIATE` | P→C 9B; C→P 1B `[00]` | P→C payload: `05 00 00 00 00 00 00 00 00` |

### `(0x85,xx)` sequence

```
phone → cam: op=(0x85,0x00)  payload=[]                      # query download state
cam → phone: op=(0x85,0x00)  5B  [00 00 ff 00 00]            # state (ff = images pending?)

phone → cam: op=(0x85,0x01)  payload=[05 00 00 00 00 00 00 00 00]  # initiate
cam → phone: op=(0x85,0x01)  1B  [00]                        # ACK

phone → cam: op=(0x85,0x00)  payload=[]                      # re-query to confirm
cam → phone: op=(0x85,0x00)  5B  [00 00 ff 00 00]            # state unchanged

# ~700 ms later — no further commands sent:
CAMERA_FUNCTION_INFO payload[4] flips 0x00 → 0x01            # camera is now ready
# poll loop detects non-zero flag → sends (0x88,00) → full pull
```

> **Open question:** The `0x05` in `(0x85,0x01)` payload may be a fixed
> constant, the queue count, or a download-mode selector. The `[00 00 ff 00 00]`
> response byte `0xff` likely indicates "images pending" but exact semantics
> are unknown.

## Transfer-ready flag (CAMERA_FUNCTION_INFO)

The app continuously polls `CAMERA_FUNCTION_INFO` (op=`0x00,0x02`
InfoType=`0x04`) as part of its idle keepalive loop. **The flag does not rise
on its own** — the phone must first send `(0x85,0x01)`, after which the camera
raises the flag ~700 ms later.

```
Normal response payload[4] = 0x00:
  00 04 03 50 [00] 00 00 00 00 00 00 05 04 01 00 00 00 00

Transfer-ready response payload[4] = 0x01 or 0x02:
  00 04 03 50 [01] 00 00 00 00 00 05 05 00 00 00 00 00 00  ← ready
  00 04 03 50 [02] 00 00 00 00 00 05 05 00 00 00 00 00 00  ← also ready
                ^^
  Fire (0x88,00) on ANY non-zero value.
```

Flag stays non-zero as long as images remain in the queue; drops to `0x00` when
the queue is empty.

## Queue behaviour

The camera maintains an internal queue of **every printed photo** pending
transfer. Each successful `(0x88,05)` dequeues one image. There is **no queue
depth field** exposed in `CAMERA_FUNCTION_INFO`:

- `img_count` in `(0x88,01)` metadata — always `1` regardless of queue depth.
- `(0x88,00)` ack `[00 00 00 00 00]` — all-zero when ready;
  **`[0x81]` (1 byte) = NACK / camera not in transfer mode** (confirmed
  2026-05-18: sending `(0x88,01)` after a `0x81` ack crashes the camera BLE
  stack — abort on any non-`[00×5]` ack).

**Drain algorithm:** loop back to polling after each successful pull; exit when
`flag == 0x00`.

**Alternative queue depth check:** Use the `(0x84,xx)` HIST sequence on connect
to get the exact pending count before any flag appears — the official app does
this on every connect (see [history-log.md](history-log.md)).

## `(0x88,xx)` transfer sequence

```
# ── Prepare: put camera into transfer mode ──────────────────────────────────
phone → cam: op=(0x85,0x00)  payload=[]                      # query download state
cam → phone: op=(0x85,0x00)  5B  [00 00 ff 00 00]
phone → cam: op=(0x85,0x01)  payload=[05 00 00 00 00 00 00 00 00]  # initiate
cam → phone: op=(0x85,0x01)  1B  [00]                        # ACK
phone → cam: op=(0x85,0x00)  payload=[]                      # re-query
cam → phone: op=(0x85,0x00)  5B  [00 00 ff 00 00]

# ── Poll until camera signals ready (~700 ms after (0x85,01)) ──────────────
[poll loop]  phone → cam: op=(0x00,0x02) InfoType=0x04  (CAMERA_FUNCTION_INFO)
             cam → phone: payload[4] = 0x00  (not ready — keep polling)
             …
             cam → phone: payload[4] != 0x00  ← TRANSFER READY

# ── Pull sequence ──────────────────────────────────────────────────────────
phone → cam: op=(0x88,0x00)  payload=[]                     # start pull request
cam → phone: op=(0x88,0x00)  5B  [00 00 00 00 00]           # camera ready ack

phone → cam: op=(0x88,0x01)  payload=[0x00 0x00 0x00 0x00]  # request metadata
cam → phone: op=(0x88,0x01)  34B  metadata                  # total_size, chunk_data_sz, timestamp

for chunk_idx in range(num_chunks):
    phone → cam: op=(0x88,0x02)  payload=[chunk_idx: uint32 BE]
    cam → phone: op=(0x88,0x02)  [img_idx:4][chunk_seq:1][jpeg_data…]

phone → cam: op=(0x88,0x03)  payload=[]                     # all chunks received
cam → phone: op=(0x88,0x03)  1B  [0x00]
phone → cam: op=(0x88,0x05)  payload=[0x00 0x00 0x00 0x00]  # transfer complete
cam → phone: op=(0x88,0x05)  1B  [0x00]
```

The camera sends **exactly one `(0x88,02)` frame per chunk** — the `[img_idx:4]`
at the start of the chunk payload was previously misparsed as a separate ack
frame.

## `(0x88,0x01)` metadata layout (34 bytes)

```
Byte  0     : 0x00  (unknown — padding or image index in batch)
Bytes 1–4   : uint32 BE — total JPEG file size in bytes
              Example: 00 03 62 4D = 221,773 bytes
Bytes 5–8   : uint32 BE — JPEG data bytes per chunk (chunk payload excl. 5B header)
              Example: 00 00 26 15 = 9,749 bytes
Bytes 9–22  : ASCII string (14 chars) — image timestamp as "YYYYMMDDHHmmss"
              Example: "20260617121452" = 2026-06-17 12:14:52 (camera internal clock)
Bytes 23–28 : 0x00 × 6  (reserved / padding)
Byte  29    : 0x32 = 50  (meaning TBD)
Byte  30    : uint8 — image count in this transfer (1 for single transfer)
Bytes 31–33 : 0x00 × 3  (reserved / padding)
```

## `(0x88,0x02)` chunk layout

```
[img_idx : uint32 BE]  — image index within transfer (0x00000000 for first/only image)
[chunk_seq: uint8]     — 0-based chunk sequence number
[jpeg_data: N bytes]   — chunk_data_size bytes of raw JPEG data
```

Reassembly:

```python
image_bytes = bytearray()
for chunk_payload in all_0x88_02_payloads:
    image_bytes.extend(chunk_payload[5:])   # skip 5-byte header
# image_bytes now contains the complete JPEG (starts with FF D8 FF)
```

## Image selection

The `(0x88,01)` request payload `[img_idx: uint32 BE]` selects which image to
transfer. Always send `[0x00 0x00 0x00 0x00]` for the first (and usually only)
image.

**Index out of range:** Requesting a non-existent index causes the camera to
reply to `(0x88,01)` with a 1-byte error `[0x81]`. The camera still accepts
the next `(0x88,00)` cleanly — it does not lock up. **To transfer a different
image, select it on the camera and press Share/Transfer again** — a new session
begins with a fresh `img_count` and the chosen image at index 0.

## Confirmed transfer examples (2026-05-17, Wide Evo)

All 4 images drained in a single BLE connection using the polling loop:

| # | Total bytes | Chunks | BLE time | Image timestamp (camera clock) |
|---|---|---|---|---|
| 1 | 221,773 | 23 (22×9749 + 7295) | ~16 s | 2026-06-17 12:14:52 |
| 2 | 235,414 | 25 (24×9749 + 1438) | ~19 s | 2026-03-24 16:17:35 |
| 3 | 235,414 | 25 (24×9749 + 1438) | ~16 s | 2026-03-24 16:17:35 (duplicate) |
| 4 | 210,761 | 22 (21×9749 + 6032) | ~12 s | 2026-01-24 20:45:20 |

BLE MTU = 247 (bonded; `pair()` required before `start_notify()`). Per-image
BLE time: ~12–19 s. Camera SD read is the bottleneck (~0.7 s/chunk).

## Gen 1 status — `(0x88,xx)` not usable

On the Mini Evo, sending `(0x88,0x00)` causes the camera to disconnect
immediately. The opcode is not supported on Gen 1 firmware. Use the photo's
internal storage + USB transfer instead. See [model-quirks.md](model-quirks.md).
