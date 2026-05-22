# Print pipeline (end-to-end)

← [Wiki index](README.md)

Confirmed working on Gen 1 Mini Evo (film ejected, image visible). All packets
use the [Link protocol framing](link-protocol.md).

Raw transfer excerpts and chunk-level traces are tracked in
[print-evidence.md](print-evidence.md).

## Step 1 — Connect and identify

```
Enable CCCD on notify char (70954784-...)
op=(0x00,0x00)  []                    SUPPORT_FUNCTION_AND_VERSION_INFO (hello)
op=(0x00,0x01)  [0x00]                IMAGE_SUPPORT_INFO → (width, height)
op=(0x00,0x01)  [0x01]                → "FUJIFILM"
op=(0x00,0x01)  [0x02]                → model ID ("FI019")
op=(0x00,0x01)  [0x03]                → serial number
op=(0x00,0x02)  [0x01]                BATTERY_INFO      → (state, pct)
op=(0x00,0x02)  [0x02]                PRINTER_FUNCTION_INFO → photos_left
```

See [session-init.md](session-init.md) for the full handshake.

## Step 2 — Send image data

```
op=(0x10,0x00)  [img_size: 4B BE]     PRINT_IMAGE_DOWNLOAD_START
    → camera ACKs with (0x10,0x00) response

for each chunk (0-based sequence number, 900 bytes each, last zero-padded):
    op=(0x10,0x01)  [seq: 4B BE] [900 bytes]  PRINT_IMAGE_DOWNLOAD_DATA
    → camera ACKs with (0x10,0x01) [seq: 4B BE]

op=(0x10,0x02)  []                    PRINT_IMAGE_DOWNLOAD_END
    → camera ACKs with (0x10,0x02)
```

- Image size = exact JPEG byte count (no header/prefix)
- Chunks are always 904 bytes in payload: 4-byte sequence + 900 bytes of data
- Last chunk is zero-padded to 900 bytes
- Each chunk is ACKed before the next is sent (no pipelining)
- Seq 0 = first chunk; `ceil(img_size / 900)` chunks total

## Step 3 — Trigger print

```
op=(0x10,0x80)  []                    PRINT_IMAGE  ← film ejects here
    → camera responds: (0x10,0x80) payload=[0x00, 0x0C]
    0x0C = 12 → confirmed "print initiated" status code
```

## Step 4 — Post-print status check

```
op=(0x00,0x02)  [0x02]                PRINTER_FUNCTION_INFO → photos_left (now decremented)
```

## Confirmed packet sizes

| Step | Payload | Total packet |
|---|---|---|
| DOWNLOAD_START | 4 bytes | 11 B |
| DOWNLOAD_DATA chunk | 904 bytes | 911 B (BLE-fragmented across writes) |
| DOWNLOAD_END | 0 bytes | 7 B |
| PRINT_IMAGE | 0 bytes | 7 B |

## Key ACK packet examples (actual bytes on wire)

```
DOWNLOAD_START ack:   61 42 00 08 10 00 00 [cs]      (8 bytes)
DOWNLOAD_DATA  ack:   61 42 00 0B 10 01 [seq 4B] [cs] (11 bytes)
DOWNLOAD_END   ack:   61 42 00 08 10 02 00 [cs]      (8 bytes)
PRINT_IMAGE    ack:   61 42 00 09 10 80 00 0C [cs]   (9 bytes)
```

---

## Image preparation (client-side)

The Mini Evo does **not** apply any image processing filters on-device. All
effects are applied on the phone (or in our Python tool) before transmission.

### Required image format

| Property | Value |
|---|---|
| Width × Height | **600 × 800 px** (portrait, Mini); **1260 × 840** (Wide); **800 × 600** (Cinema) |
| Color mode | RGB |
| File format | JPEG |
| Target size | **94.5 – 105 KB** (binary-search quality) |
| Max quality | 95 (prevents quality=100 inflated files) |

Always query `IMAGE_SUPPORT_INFO` (`op=(0x00,0x02)` InfoType=0x00) for the
authoritative dimensions; never hard-code by model name.

The size ceiling (105 KB) was determined empirically to fit camera buffer
constraints.

### Resize behaviour

Input images are scaled to fit the target dimensions with `LANCZOS` resampling,
then center-cropped (or letterboxed by PIL's `thumbnail` default). Portrait
images fill the frame; landscape images are letterboxed top/bottom.

### Image modes ("filters")

These are **client-side PIL operations** applied before JPEG encode; the camera
receives only the processed pixel data.

| Mode | Implementation | Effect |
|---|---|---|
| **Normal** | No change | Native colours |
| **Rich** | `ImageEnhance.Color(img).enhance(1.5)` | Saturation ×1.5 |

The "Rich" name matches the Instax app's filter name. Other official app
filters (Fade, Mono, Sepia, etc.) can be approximated with standard PIL
operations. None of these require any additional BLE command — the camera is
always in "raw receive" mode for image data.
