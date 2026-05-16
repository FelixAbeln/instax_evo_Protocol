# Instax Evo BLE Protocol Notes

Analysis of the BLE protocol used by the Instax camera/printer family.
All findings derived from Android bugreport HCI captures cross-referenced with
[javl/InstaxBLE](https://github.com/javl/InstaxBLE).

---

## Key Insight: Two BLE Profiles, Two Protocols

Every Instax camera with BLE advertises **two separate BLE profiles** simultaneously:

| Profile | BLE address prefix | Protocol | Used by |
|---|---|---|---|
| **IOS** | `FA:AB:BC:xx:xx:xx` | Link protocol (`41 62` / `61 42` framing) | Instax iOS app, javl/InstaxBLE, **this project** |
| **Android** | `E0:48:24:xx:xx:xx` (Mini Evo) | Legacy binary (`16xx`/`17xx` writes) | Instax Android app only |

Both profiles share the **same GATT service and characteristic UUIDs** but speak entirely different application protocols.

> **javl/InstaxBLE only connects to `(IOS)` profiles** — its filter is `INSTAX-` prefix + `(IOS)` suffix.
> Our initial bugreport capture (17-34-32) used the **Android profile** — a red herring for the Link protocol.
> The correct target for all polling/printing code is the **IOS BLE profile**.

---

## Confirmed Camera Models

| Model | Model ID | Gen | BLE IOS address | BLE Android address | Film | Shots remaining |
|---|---|---|---|---|---|---|
| Instax Mini Evo | **FI019** | 1 | `FA:AB:BC:11:6F:D2` | `E0:48:24:D7:CF:2E` | Instax Mini (600×800) | 1 ✓ live |
| Instax Evo Wide | **FI028** | 2 | `FA:AB:BC:1D:0A:7B` | — | Instax Wide (1260×840) | 4 ✓ HCI log |
| Instax Mini Evo Cinema | unknown | 3 | unknown (not captured) | — | Instax Mini | — |

Notes:
- Gen 1 BR/EDR address `88:B4:36:11:6F:D2` is a Fujifilm-OUI classic Bluetooth address — **not BLE**.
- Model IDs from `DEVICE_INFO_SERVICE` op=(0x00,0x01) InfoType=1: FI019 (Mini Evo), FI028 (Evo Wide).
- BLE device name suffix = serial number: `INSTAX-3332137670 (IOS)` serial = "3332137670".
- Gen 1 **requires passkey/PIN pairing** after firmware update (even on IOS profile). Call `pair()` before subscribing.
- Gen 3 (Mini Evo Cinema) is not in our possession; assumed to use the same Link protocol.

---

## Shared GATT Service (all models, both profiles)

| UUID | Role |
|---|---|
| `70954782-2d83-473d-9e5f-81e1d02d5273` | Instax primary service |
| `70954783-2d83-473d-9e5f-81e1d02d5273` | **Write characteristic** (Write + WriteNoResp) |
| `70954784-2d83-473d-9e5f-81e1d02d5273` | **Notify characteristic** (subscribe for responses) |

These UUIDs are shared across all known models and both profiles.

---

## GATT Handle Layout

### Gen 1 – Instax Mini Evo, IOS profile (`FA:AB:BC:11:6F:D2`)

Recovered from live probe session (not from HCI log):

| Handle | Props | UUID | Role |
|---|---|---|---|
| h=0x0014 | Write, WriteNoResp | `70954783-...` | Write char |
| h=0x0016 | Notify | `70954784-...` | Notify char |
| h=0x0018 | — | `0x2902` CCCD | Write `01 00` to enable notifications |

### Gen 1 – Instax Mini Evo, Android profile (`E0:48:24:D7:CF:2E`)

From 17-34-32 HCI capture:

| Handle | Props | Role |
|---|---|---|
| h=0x0020 | Write | Generic write channel |
| h=0x001D | Notify | Notifications from h=0x0020 channel |
| h=0x002A | Write | DEVICE_ID-auth write channel |
| h=0x0027 | Notify | Notifications from h=0x002A channel |

### Gen 2 – Instax Evo Wide (`FA:AB:BC:1D:0A:7B`)

Full GATT table from 19-51-52 HCI capture:

| Handle range | Service UUID | Purpose |
|---|---|---|
| 0x0001–0x0004 | `0x1801` Generic Attribute | Service Changed (h=0x0003, CCCD h=0x0004) |
| 0x0005–0x000D | `0x1800` Generic Access | Device name, appearance, etc. |
| **0x000E–0x0013** | `70954782-2d83-473d-9e5f-81e1d02d5273` | **Instax primary service** |
| 0x0014–0x0026 | `0x180A` Device Information | DIS — manufacturer, model, serial, FW |
| 0x0027–0x003B | `0000d0ff-3c17-d293-8e48-14fe2e4da212` | Fujifilm secondary service |
| 0x003C–0xFFFF | `00006287-3c17-d293-8e48-14fe2e4da212` | Fujifilm tertiary service |

Instax primary service characteristics (gen 2):

| Handle | Props | UUID | Role |
|---|---|---|---|
| **h=0x0010** | Write, WriteNoResp | `70954783-...` | **Write char** |
| **h=0x0012** | Notify | `70954784-...` | **Notify char** |
| **h=0x0013** | — | `0x2902` CCCD | Write `01 00` to enable notifications |

Device Information service (gen 2):

| Handle | UUID | Returns |
|---|---|---|
| h=0x0016 | `0x2A29` Manufacturer Name | `"FUJIFILM"` |
| h=0x0018 | `0x2A24` Model Number | `"FI028"` |
| h=0x001A | `0x2A25` Serial Number | `"92007814"` |
| h=0x001C | `0x2A27` Hardware Revision | (unknown) |
| h=0x001E | `0x2A26` Firmware Revision | (unknown) |
| h=0x0020 | `0x2A28` Software Revision | (unknown) |
| h=0x0022 | `0x2A23` System ID | (unknown) |
| h=0x0024 | `0x2A2A` Regulatory | (unknown) |
| h=0x0026 | `0x2A50` PnP ID | (unknown) |

Fujifilm secondary service chars (h=0x0027–0x003B):

| Handle | Props | UUID | Purpose |
|---|---|---|---|
| h=0x0029 | WriteNoResp | `0xFFD1` | Unknown (possibly OTA write) |
| h=0x002B | Read | `0xFFD2` | Unknown |
| h=0x002D | Read | `0xFFD3` | Unknown |
| h=0x002F | Read | `0xFFD4` | Unknown |
| h=0x0031 | Read | `0xFFF1` | Unknown |
| h=0x0033 | Read | `0xFFE0` | Unknown |
| h=0x0035 | Read | `0xFFE1` | Unknown |
| h=0x0037 | Read | `0xFFF3` | Unknown |
| h=0x0039 | Read | `0xFFF4` | Unknown |
| h=0x003B | Read | `0xFFF5` | Unknown |

Fujifilm tertiary service chars (h=0x003C–0xFFFF):

| Handle | Props | UUID | Purpose |
|---|---|---|---|
| h=0x003E | WriteNoResp | `00006387-3c17-d293-8e48-14fe2e4da212` | Unknown write |
| h=0x0040 | Write, Notify | `00006487-3c17-d293-8e48-14fe2e4da212` | Unknown cmd+notify |
| h=0x0041 | — | `0x2902` CCCD | CCCD for h=0x0040 |

---

## Link Protocol (IOS profile — all models)

This is the **javl/InstaxBLE** protocol, used by the Instax iOS app and all IOS BLE profiles.
The Instax Android app does **not** use this protocol.

### Packet format

```
Request:   41 62  [length: uint16 BE]  [op1]  [op2]  [payload...]  [checksum]
Response:  61 42  [length: uint16 BE]  [op1]  [op2]  [payload...]  [checksum]
```

- `41 62` = `"Ab"` — phone to printer
- `61 42` = `"aB"` — printer to phone
- `length` = total packet size in bytes (including the 2-byte header and 1-byte checksum)
- `checksum` = `(255 - (sum(all_preceding_bytes) & 255)) & 255`
- Minimum packet (no payload) = 7 bytes: `41 62 00 07 [op1] [op2] [cs]`

> **How to read the `00` in `41 62 00 07 ...`:** The `00` is the *high byte* of the 2-byte big-endian length
> field (since BLE packets are always < 256 bytes, the high byte is always `0x00`). There is **no** 3-byte
> header — the format is `[41 62] [00 07]` = header(2) + length(2), not `[41 62 00] [07]`.

Checksum identity: `(sum(entire_packet) & 255) == 255`

Packets > ~182 bytes are split into multiple BLE write commands; reassemble before parsing.

### Python packet builder

```python
import struct

def create_packet(op1: int, op2: int, payload: bytes = b'') -> bytes:
    """Build an Instax Link protocol request packet."""
    header = b'\x41\x62'
    length = struct.pack('>H', 7 + len(payload))
    body   = header + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])

def validate_checksum(packet: bytes) -> bool:
    return (sum(packet) & 255) == 255
```

### EventType op codes

Sourced from [javl/InstaxBLE `Types.py`](https://github.com/javl/InstaxBLE/blob/main/Types.py),
cross-referenced with the gen 2 Evo Wide HCI capture:

| op1 | op2 | Name | Notes |
|---|---|---|---|
| 0x00 | 0x00 | `SUPPORT_FUNCTION_AND_VERSION_INFO` | Init/hello — first packet sent |
| 0x00 | 0x01 | `DEVICE_INFO_SERVICE` | Device info (payload = InfoType byte) |
| 0x00 | 0x02 | `SUPPORT_FUNCTION_INFO` | Status/battery poll (payload = InfoType byte) |
| 0x00 | 0x10 | `IDENTIFY_INFORMATION` | |
| 0x01 | 0x00 | `SHUT_DOWN` | |
| 0x01 | 0x02 | `AUTO_SLEEP_SETTINGS` | |
| 0x10 | 0x00 | `PRINT_IMAGE_DOWNLOAD_START` | |
| 0x10 | 0x01 | `PRINT_IMAGE_DOWNLOAD_DATA` | Chunked image bytes |
| 0x10 | 0x02 | `PRINT_IMAGE_DOWNLOAD_END` | |
| 0x10 | 0x80 | `PRINT_IMAGE` | Trigger the print |
| 0x10 | 0x81 | `REJECT_FILM_COVER` | |
| 0x20 | 0x00 | `FW_DOWNLOAD_START` | Firmware update |
| 0x20 | 0x10 | `FW_PROGRAM_INFO` | Firmware version query — seen in gen 2 |
| 0x30 | 0x00 | `XYZ_AXIS_INFO` | Accelerometer |
| 0x30 | 0x01 | `LED_PATTERN_SETTINGS` | |
| 0x80 | 0x00 | `CAMERA_SETTINGS` | Evo-specific camera setting write |
| 0x80 | 0x01 | `CAMERA_SETTINGS_GET` | Evo-specific camera setting read |
| 0x80 | 0x10 | *(Evo-specific)* | Read config register bank (gen 2 observed) |
| 0x80 | 0x11 | *(Evo-specific)* | Read individual register (payload = reg ID + 4 zero bytes) |
| 0x84 | 0x00 | `CAMERA_LOG_SUBTOTAL_START` | Film remaining query — **confirmed gen 2** |
| 0x84 | 0x01 | `CAMERA_LOG_SUBTOTAL_DATA` | |
| 0x84 | 0x02 | `CAMERA_LOG_SUBTOTAL_CLEAR` | |
| 0x84 | 0x03 | `CAMERA_LOG_DATE_START` | |
| 0x84 | 0x06 | `CAMERA_LOG_FILTER_START` | |

### InfoType payload values

Used as the single payload byte in `DEVICE_INFO_SERVICE` (op1=0x00, op2=0x01) and `SUPPORT_FUNCTION_INFO` (op1=0x00, op2=0x02) requests:

| Value | Name | Notes |
|---|---|---|
| 0x00 | `IMAGE_SUPPORT_INFO` | Response payload: two BE uint16 = (width, height); 600×800 mini, 800×800 square, 1260×840 wide |
| 0x01 | `BATTERY_INFO` | Response payload bytes 0–1: `[battery_state][battery_pct]` |
| 0x02 | `PRINTER_FUNCTION_INFO` | Response payload byte 0: `photos_left = byte & 0x0F`, `charging = byte & 0x80` |
| 0x03 | `PRINT_HISTORY_INFO` | |
| 0x04 | `CAMERA_FUNCTION_INFO` | |
| 0x05 | `CAMERA_HISTORY_INFO` | |

### Status query sequence (IOS profile, any model)

```python
# Connect — NO pairing required for IOS profile
# Enable notifications on notify char 70954784-...

# 1. Hello
pkt = create_packet(0x00, 0x00)               # SUPPORT_FUNCTION_AND_VERSION_INFO
# 2. Image size → determines film format (mini/square/wide)
pkt = create_packet(0x00, 0x01, b'\x00')      # DEVICE_INFO_SERVICE IMAGE_SUPPORT_INFO
# 3. Battery
pkt = create_packet(0x00, 0x02, b'\x01')      # SUPPORT_FUNCTION_INFO BATTERY_INFO
# 4. Photos left
pkt = create_packet(0x00, 0x02, b'\x02')      # SUPPORT_FUNCTION_INFO PRINTER_FUNCTION_INFO
```

### Parsing status responses

All responses use the same framing: `61 42 [len:2B_BE] [op1] [op2] [payload...] [checksum]`.
Payload starts at byte offset 6 (`response[6:]`).

**Payload prefix:** `SUPPORT_FUNCTION_INFO` and `DEVICE_INFO_SERVICE` responses include a
2-byte prefix before the actual data: `[0x00][InfoType_echo]`. Actual data starts at `payload[2]`
(= `response[8]`), which is exactly what javl indexes as `packet[8:]`.

**Battery (`SUPPORT_FUNCTION_INFO` + `BATTERY_INFO`):**

```python
# response[6] = 0x00, response[7] = 0x01 (InfoType echo)
battery_state, battery_pct = struct.unpack_from('>BB', response, 8)
# battery_state: 0=critical, 1=low, 2=medium, 3=high, 4=full
# battery_pct: 0–100
# Live example (Gen 1, after firmware update): state=3 (HIGH), pct=32
```

**Photos left (`SUPPORT_FUNCTION_INFO` + `PRINTER_FUNCTION_INFO`):**

```python
# response[6] = 0x00, response[7] = 0x02 (InfoType echo)
status_byte = response[8]
photos_left = status_byte & 0x0F    # low 4 bits (max 10 for a standard pack)
is_charging = bool(status_byte & 0x80)
# Live example (Gen 1): status_byte=0x31, photos_left=1 ✓
```

**Image size (`SUPPORT_FUNCTION_INFO` + `IMAGE_SUPPORT_INFO`):**

```python
# response[6] = 0x00, response[7] = 0x00 (InfoType echo)
width, height = struct.unpack_from('>HH', response, 8)
# Gen 1 Mini Evo: 600×800; Gen 2 Evo Wide: 1260×840
```

**Device strings (`DEVICE_INFO_SERVICE`):**

```python
# response payload: [0x00][InfoType][str_len][str_bytes...]
# InfoType 0 → Manufacturer ("FUJIFILM")
# InfoType 1 → Model ID    ("FI019" Mini Evo, "FI028" Evo Wide)
# InfoType 2 → Serial      ("3332137670" — same as BLE name suffix)
# InfoType 3 → "0000"
str_len = response[8]
text = response[9:9 + str_len].decode('ascii')
```

---

## Gen 2 (Evo Wide) — Observed BLE Session

From 19-51-52 HCI capture. All 4 captured BLE connections are **identical** — no session state changes.

### Connection sequence

```
Write h=0x0013 = 01 00                Enable CCCD for notify char h=0x0012

op=(0x00,0x00)  []                    SUPPORT_FUNCTION_AND_VERSION_INFO
op=(0x00,0x01)  [0x00]                IMAGE_SUPPORT_INFO → 1260×840 (Wide confirmed)
op=(0x00,0x01)  [0x01]                → "FUJIFILM"
op=(0x00,0x01)  [0x02]                → "FI028" (model)
op=(0x00,0x01)  [0x03]                → "92007814" (serial)
op=(0x00,0x01)  [0x04]                → "0000"
op=(0x00,0x01)  [0x05]                → "0100"
op=(0x00,0x01)  [0x09]                → (empty)
op=(0x00,0x01)  [0x0A]                → (empty)
op=(0x00,0x02)  [0x00]                SUPPORT_FUNCTION_INFO IMAGE_SUPPORT_INFO → 1260×840
op=(0x20,0x10)  []                    FW_PROGRAM_INFO → firmware version bytes
op=(0x80,0x10)  []                    [Evo] config register bank → 0x00020003
op=(0x80,0x11)  [0x0B, 0,0,0,0]      [Evo] read reg 0x0B → 2
op=(0x80,0x11)  [0x0C, 0,0,0,0]      → 0
op=(0x80,0x11)  [0x13, 0,0,0,0]      → 0
op=(0x80,0x11)  [0x14, 0,0,0,0]      → 0
op=(0x80,0x11)  [0x15, 0,0,0,0]      → 0
op=(0x80,0x11)  [0x16, 0,0,0,0]      → 0x32 = 50  (possibly total prints taken)
op=(0x80,0x11)  [0x17, 0,0,0,0]      → 0x01 = 1
op=(0x80,0x11)  [0x18..0x1A, ...]    → 0
op=(0x80,0x11)  [0x1B, 0,0,0,0]      → 1
op=(0x84,0x00)  []                    CAMERA_LOG_SUBTOTAL_START → film remaining = 4 ✓
op=(0x84,0x01)  []                    → (zeros)
[periodic keepalive notifications from camera]
```

### Film remaining — Evo Wide

`CAMERA_LOG_SUBTOTAL_START` (op=0x84,0x00) response payload (bytes 6–17):
```
00 00 00 00  04 00 00 00  04 00 00 00
             ^^^^^^^^^^^
             uint32 LE = 4  ← photos remaining (confirmed by user observation: 4 shots left)
```

### Image size — Evo Wide

`SUPPORT_FUNCTION_INFO` IMAGE_SUPPORT_INFO response payload (bytes 6+):
```
04 EC  03 48  ...
^^^^^  ^^^^^
0x04EC=1260   0x0348=840  ← Instax Wide film dimensions ✓
```

---

## Legacy Android Protocol (gen 1 only, not used in this project)

Only valid on the Android BLE profile (`E0:48:24:D7:CF:2E`). Captured in 17-34-32 HCI log.

### Device-specific DEVICE_ID (8 bytes, gen 1 Mini Evo)

```
8d 3d b0 e5 92 59 03 3d
```

### Handshake writes

```
Write h=0x002A: 00 05  [DEVICE_ID 8b]  00 00        (12 bytes, WriteCommand)
Write h=0x0020: 00 05  01 00 00 00 00 00 00 00 00 00
Write h=0x002A: 00 00  [DEVICE_ID 8b]  04 00 00
Write h=0x0020: 00 00  01 00 00 00 00 00 00 00 04 00 00
```

### Status poll commands (post-handshake)

```
Write h=0x002A: 16 00   (poll init)
Write h=0x0020: 17 00
Write h=0x002A: 16 01   → Notify h=0x0027: 16 01 00 03 44  (battery level = 3 HIGH)
Write h=0x002A: 16 02   → Notify h=0x0027: 16 02 01 02 02  (film remaining = 1)
```

Decoding:
- `16 01 00 03 44`: battery_level = byte[3] = 0x03 (scale 0–4, confirmed "3 pips" full)
- `16 02 01 02 02`: film count field = byte[2] = 0x01 → 1 shot remaining

### Keep-alive pings (Android protocol)

Every ~25 seconds: `19 00 [seq]` / `1B 00 [seq]` on h=0x0027 / h=0x001D.
Sequence counter is global across BLE connection sessions.

---

## Known Film Counts (confirmed)

| Camera | Film remaining | Source |
|---|---|---|
| Gen 1 Mini Evo (FI019) | 1 shot | `PRINTER_FUNCTION_INFO` response[8] & 0x0F = 1 ✓ live |
| Gen 1 Mini Evo (FI019) | 1 shot | Android protocol `16 02` response byte[2] (cross-check) |
| Gen 2 Evo Wide (FI028) | 4 shots | `PRINTER_FUNCTION_INFO` response[8] & 0x0F (HCI log, inferred) |

> `CAMERA_LOG_SUBTOTAL_START` (op=0x84,0x00) returns **lifetime shot counts**, not remaining shots.
> Live: Gen 1 returned 148 (total shots taken over camera lifetime).
> Use `PRINTER_FUNCTION_INFO` for shots remaining.

---

## Capture Log Files

| File | Camera | Profile | Notes |
|---|---|---|---|
| `captures/extracted/.../17-34-32/btsnoop_hci.log` | Gen 1 Mini Evo | **Android** | Full print session decoded; battery + film count confirmed |
| `captures/extracted/19-51-52/FS/data/log/bt/btsnoop_hci.log` | Gen 2 Evo Wide | **IOS** | 4 identical BLE connections; full Link protocol decoded |
| `captures/extracted/19-51-52/.../btsnoop_hci.log.last` | Mixed | — | Also contains BR/EDR traffic from an Instax printer |

---

## Connection Notes

- **IOS profile requires passkey/PIN pairing** (at least on Gen 1 after firmware update). The user must pair once via Windows Bluetooth settings (a 6-digit code appears on screen or in the app).
- After pairing, call `client.pair()` in bleak before subscribing — this re-establishes the encrypted session for the current connection. Without it, CCCD writes fail with "Operation aborted".
- `pair()` returns `None` when already bonded (correct — the encrypted session is still established).
- If the camera's firmware was updated, its bond database is wiped. Remove the INSTAX entry from Windows Bluetooth settings and re-pair.
- Android profile requires pairing + DEVICE_ID auth. Not used by this project.
- javl's device scanner: `foundName.startswith('INSTAX-') and foundName.endswith('(IOS)')`
- BLE device name format: `INSTAX-[serial] (IOS)` where serial matches `DEVICE_INFO_SERVICE` InfoType=2.
- The gen 2 Evo Wide IOS profile name is `INSTAX-[serial] (IOS)` (serial from DIS, not address-derived).

---

## End-to-End Print Pipeline (confirmed working — Gen 1 Mini Evo)

The following sequence was reverse-engineered from official app BLE captures and
validated by a successful physical print (film ejected, image visible). All packets
use the [Link protocol framing](#link-protocol-ios-profile--all-models).

### Step 1 — Connect and identify

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

### Step 2 — Send image data

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

### Step 3 — Trigger print

```
op=(0x10,0x80)  []                    PRINT_IMAGE  ← film ejects here
    → camera responds: (0x10,0x80) payload=[0x00, 0x0C]
    0x0C = 12 → confirmed "print initiated" status code
```

### Step 4 — Post-print status check

```
op=(0x00,0x02)  [0x02]                PRINTER_FUNCTION_INFO → photos_left (now decremented)
```

### Confirmed packet sizes

| Step | Payload | Total packet |
|---|---|---|
| DOWNLOAD_START | 4 bytes | 11 B |
| DOWNLOAD_DATA chunk | 904 bytes | 911 B (BLE-fragmented across writes) |
| DOWNLOAD_END | 0 bytes | 7 B |
| PRINT_IMAGE | 0 bytes | 7 B |

### Key ACK packet examples (actual bytes on wire)

```
DOWNLOAD_START ack:   61 42 00 08 10 00 00 [cs]      (8 bytes)
DOWNLOAD_DATA  ack:   61 42 00 0B 10 01 [seq 4B] [cs] (11 bytes)
DOWNLOAD_END   ack:   61 42 00 08 10 02 00 [cs]      (8 bytes)
PRINT_IMAGE    ack:   61 42 00 09 10 80 00 0C [cs]   (9 bytes)
```

---

## Image Preparation (client-side)

The Instax Mini Evo does **not** apply any image processing filters on-device.
All effects are applied on the phone (or in our Python tool) before transmission.

### Required image format

| Property | Value |
|---|---|
| Width × Height | **600 × 800 px** (portrait) |
| Color mode | RGB |
| File format | JPEG |
| Target size | **94.5 – 105 KB** (binary-search quality) |
| Max quality | 95 (prevents quality=100 inflated files) |

The 600×800 dimensions are returned by `IMAGE_SUPPORT_INFO` (`op=(0x00,0x01)` InfoType=0x00).
The size ceiling (105 KB) was determined empirically to fit camera buffer constraints.

### Resize behaviour

Input images are scaled to fit 600×800 with `LANCZOS` resampling, then center-cropped
(or letterboxed by PIL's `thumbnail` default). Portrait images fill the frame; landscape
images are letterboxed top/bottom.

### Image modes ("filters")

These are **client-side PIL operations** applied before JPEG encode; the camera
receives only the processed pixel data.

| Mode | Implementation | Effect |
|---|---|---|
| **Normal** | No change | Native colours |
| **Rich** | `ImageEnhance.Color(img).enhance(1.5)` | Saturation ×1.5 — vivid, punchy colours |

> The "Rich" name matches the Instax app's filter name. Other official app filters
> (Fade, Mono, Sepia, etc.) can be approximated with standard PIL operations.
> None of these require any additional BLE command — the camera is always in "raw
> receive" mode for image data.

---

## Known Gaps — Commands Not Yet Identified

### Print history / transferred-images gallery

**Symptom:** After a successful print, the image does not appear in the camera's
internal "printed images" list (visible in the Instax app's history view and on-device
gallery).

**Hypothesis:** The official app sends one or more additional BLE commands — either
alongside or after the print sequence — to register the image in the camera's history
log. The opcode(s) are not yet known.

**Candidates:**

| Candidate | Reasoning |
|---|---|
| `CAMERA_LOG_SUBTOTAL_START` (0x84,0x00) | Already used for film-remaining; may have a write variant |
| `CAMERA_SETTINGS` (0x80,0x00) with metadata payload | Writes a setting/config; could include image reference |
| Unknown opcode in 0x84 family (0x84,0x03 `CAMERA_LOG_DATE_START`) | Date-tagged log entry for each print |
| `SUPPORT_FUNCTION_INFO` InfoType 0x03 `PRINT_HISTORY_INFO` | Query — but write equivalent may register entry |
| Unknown opcode not yet observed | E.g. 0x84,0x04 / 0x84,0x05 / 0x90,0x00 |

**Next step:** Capture a full print session from the official Instax iOS app
(Android bugreport HCI log) and diff the BLE write sequence against our implementation.
Focus on any `op=(0x84,xx)` or `op=(0x80,xx)` writes that our tool does not send.

See [captures/README.md](../captures/README.md) for how to collect a bugreport capture.

---

## Local Print Log

Every `evo-print` run appends a record to `captures/print-log.jsonl`:

```json
{
  "t": 1747397000.0,
  "image": "F:\\path\\to\\image.jpg",
  "camera": "FA:AB:BC:11:6F:D2",
  "model": "FI019",
  "transferred": true,
  "printed": false,
  "photos_left_after": 1
}
```

| Field | Meaning |
|---|---|
| `t` | Unix timestamp of the operation |
| `image` | Absolute path to the source image file |
| `camera` | BLE address of the camera (IOS profile) |
| `model` | Model ID from `DEVICE_INFO_SERVICE` (e.g. `"FI019"`) |
| `transferred` | `true` if image data was fully sent to camera |
| `printed` | `true` if `PRINT_IMAGE` (0x10,0x80) was also sent (film ejected) |
| `photos_left_after` | `photos_left` value from post-print status poll |

`transferred=true, printed=false` means `--enable-print` was not passed — image was
sent but film was not ejected (safe test mode).

---

## References

- [javl/InstaxBLE](https://github.com/javl/InstaxBLE) — Python library for Instax Link printers (Mini/Square/Wide Link) via IOS BLE profile. Protocol is structurally identical to what Evo Wide uses.
- [javl/InstaxBLE `Types.py`](https://github.com/javl/InstaxBLE/blob/main/Types.py) — EventType and InfoType enumerations
- [javl/InstaxBLE issue #4](https://github.com/javl/InstaxBLE/issues/4#issuecomment-1484123671) — Android bugreport HCI capture guide
- [jpwsutton/instax_api](https://github.com/jpwsutton/instax_api) — older WiFi-based Instax protocol

---

## Appendix: First-pass Android capture notes (17-34-32, now superseded)

Method:

- Parsed btsnoop records directly (HCI ACL -> L2CAP CID 0x0004 ATT only)
- Direction labels follow btsnoop flag bit 0 decoding
- Handle numbers are ATT handles (UUID mapping still pending from live `inspect`)

## Device identity

- Device:
- BLE name:
- Address / Windows device ID:
- Advertised service UUIDs:
- Manufacturer data:
- Requires pairing/bonding:

## GATT services

| Service UUID | Purpose | Notes |
|---|---|---|

## Characteristics

| UUID | Properties | Direction | Suspected purpose |
|---|---|---|---|

## Capture summary (2026-05-16 bugreport)

- Main ATT log (`...btsnoop_hci.log.last.log`): 1,253 ATT packets
- Direction counts: 641 device_to_host, 612 host_to_device
- Dominant opcodes:
  - Handle Value Notification: 491
  - Write Command: 462
  - Read Request: 57
  - Read Response: 54
  - Read By Type Request: 44
  - Error Response: 35
- Most active ATT handles:
  - 0x0020 (407 events)
  - 0x001D (365 events)
  - 0x0027 (126 events)
  - 0x002A (56 events)

Likely high-traffic data paths are between handles `0x0020`/`0x002A` and notification handles `0x001D`/`0x0027`.

## Observed session flow

1. Connect
2. ATT MTU exchange (`Exchange MTU Request/Response`)
3. GATT discovery phase:
   - `Read By Group Type Request/Response`
   - `Read By Type Request/Response`
   - `Find Information Request/Response`
4. Setup writes (`Write Request` with matching `Write Response`)
5. Bulk transfer phase:
   - Heavy `Write Command` traffic to handles `0x0020` and `0x002A`
   - Frequent `Handle Value Notification` from `0x001D` and `0x0027`
6. Occasional `Read Request/Response` checks (likely status/config)
7. Bursty session pattern with idle gaps between activity windows

First burst excerpt (relative capture time):

- `t=2.664s` MTU exchange begins
- `t=2.905s - 3.235s` discovery + setup writes
- `t=3.239s` write-command stream starts (`0x002A`, `0x0020`)
- `t=3.297s` first high-rate notifications (`0x0027`, then `0x001D`)

Detected burst windows (split on >20s idle gap, first ten):

- 2.66s-36.08s (345 ATT packets)
- 56.25s-81.26s (99 ATT packets)
- 105.19s-106.28s (2 ATT packets)
- 130.30s-131.21s (2 ATT packets)
- 155.38s-156.23s (2 ATT packets)
- 180.27s-181.25s (2 ATT packets)
- 205.52s-206.23s (2 ATT packets)
- 230.27s-231.43s (2 ATT packets)
- 255.56s-260.73s (22 ATT packets)
- 281.38s-285.70s (2 ATT packets)

## Working hypotheses

- `0x0020` and `0x002A` are likely host-write channels for payload/control chunks.
- `0x001D` and `0x0027` are likely device notification channels for ack/status/data fragments.
- The burst pattern is consistent with repeated mini-jobs (possibly segmented transfer, spool, or status polling loops).
- `Handle Value Indication` and `Handle Value Confirmation` are rare and likely used for control-state boundaries.

## Handle-to-UUID mapping (extracted from GATT discovery packets)

Standard GATT services identified:

- **0x1801**: Generic Attribute (GATT)
- **0x1800**: Generic Access (GAP)
- **0x180A**: Device Information
- **0x1849**: Custom vendor service (primary traffic)
- **0x184C**: Custom vendor service (secondary)
- **0x5511be183c4754781fc24b4b5dcdaf62**: Custom 128-bit vendor UUID

Active handles in main session (from earlier analysis):

| Handle | Service UUID | Notes |
|--------|------|-------|
| 0x001D | 0x1849 | High-freq device notifications (365 events) |
| 0x0020 | 0x1849 | High-freq host writes (407 events) |
| 0x0027 | 0x1849 | Moderate-freq device notifications (126 events) |
| 0x002A | 0x1849 | Host write channel (56 events) |
| 0x000D | 0x1800 | GAP characteristic (setup writes early session) |
| 0x0013 | 0x180A | Device Info characteristic |
| 0x001E | 0x1800 | GAP write target |
| 0x0016 | 0x180A | Device Info read source |
| 0x0028 | 0x1849 | Low-activity custom handle |
| 0x0003 | 0x1800 | GAP indication target (rare) |

## Multi-capture session analysis

Multiple Android bugreport captures (May 16, 2026):
- **17-34-32** (first session): Full lifecycle with image send (~1254s total) ✓ Confirmed successful print
- **17-43-18** (intermediate): Partial session data
- **17-52-45** (third session): Keep-alive phase after connect/disconnect (125s)

### Evidence of successful transmission

The image sent via BLE in session 17-34-32 was successfully printed on thermal media. Observed device output shows:
- Thermal printer produced physical receipt/photo output
- Battery level indicator: device displays 3 "pips" for full battery in UI status notifications
- Image data integrity verified by successful physical output

### Session phases observed

**Phase 1: Connection & GATT Discovery (0-10s)**
- Device advertises with service UUID 70954782-2d83-...
- Client initiates connection and MTU exchange
- GATT database discovery packets on handles 0x0001-0x0060
- Status notifications begin on 0x001D with "02 01" / "02 02" patterns (image count field)

**Phase 2: Image Data Transmission (3-7s in session 1)**
- Bulk write commands to handle 0x0020 (Write Command channel)  
- Corresponding notifications from device on 0x001D (status updates)
- Status packets show transitions in image count tracking
- Payload patterns suggest chunked image transfer (max ~100-200 bytes/packet)

**Phase 3: Completion & Status Polling (7-1254s)**
- Device sends status notifications every ~25 seconds on 0x001D
- Keep-alive ping pattern: small status packets (4-8 bytes) alternating between different handles
- Structure: `1B 00 4C`, `1B 00 4D`, `1B 00 4E` (incrementing counter)
- Final keep-alive at 1254s before disconnect

### Decoded notify packet types

All notify packets arrive on handles **0x001D** and **0x0027** (mirrored — same data on both channels). Decoded from live HCI analysis:

#### Type A: 5-byte Status Message — `[type] [subtype] [value] [b3] [b4]`

| Subtype | Payload example | Meaning |
|---------|-----------------|---------|
| `01` | `16 01 00 03 44` | **Init/battery**: `b3 = battery level (0-3 pips)`. Confirmed: `03` = full (3 pips) |
| `02` | `16 02 01 02 02` | **Image count**: `b2 = images currently queued`. Observed: `01` = 1 image loaded |

- `type` byte: `0x16` = msg on 0x0027, `0x17` = mirror on 0x001D
- Battery field is **byte [3]** of 5-byte packets with subtype `01`
- Image count field is **byte [2]** of 5-byte packets with subtype `02`

**Concrete example at T=0.870s (session 17:34:32):**
```
16 01 00 03 44
^  ^  ^  ^  ^
|  |  |  |  +-- checksum / footer
|  |  |  +---- battery = 0x03 = 3 pips (full)
|  |  +------- reserved
|  +----------- subtype: 01 = init/battery message
+-------------- message type 0x16 (channel 0x0027)
```

**Concrete example at T=0.932s (session 17:34:32):**
```
16 02 01 02 02
^  ^  ^  ^  ^
|  |  |  +--+-- extra state bytes (02 02)
|  |  +-------- image count = 0x01 = 1 image queued
|  +----------- subtype: 02 = image count message
+-------------- message type 0x16
```

#### Type B: 3-byte Keep-Alive Ping — `[msg_id] [00] [seq]`

```
19 00 2E
^  ^  ^
|  |  +-- sequence number (increments per ping, ~25s interval)
|  +----- constant 0x00 separator
+-------- message ID: 0x19 (ch 0x0027) / 0x1B (ch 0x001D)
```

- Channel 0x0027 pings: seq 0x00 → 0x2F (every ~25s)
- Channel 0x001D pings: seq 0x00 → 0x2B (slightly offset, every ~25s)
- Session 3 (17:52:45) continues from seq `0x56`, confirming global counter across connections

#### Type C: 6-byte Device ID/Firmware — `[type] [sub] [4 bytes]`

Example: `16 00 D6 B7 7B 1B` — appears once at session start, likely firmware version or device ID bytes.

#### Type D: 13-byte Session Init — `[header] [6-byte device_id] [padding] [state]`

Example: `00 06 8D 3D B0 E5 92 59 03 3D 00 00 01`
- Bytes [2-7]: 6-byte device identifier (changes per device connection)
- Last byte `01`: initial state flag

### Status message format (Handle 0x001D notifications starting with 0xA8)

```
A8 [seq] 00 [type] [data...]
   ^      ^  ^      ^
   |      |  |      +-- Message type: 0x02 (status), 0x52 (completion)
   |      |  +---------- Reserved
   |      +------------- Sequence number (increments with each message)
   +------------------- Message class (0xA8 = status notification)
```

**Status type 0x02 (During setup/transfer):**
- Offset +4: `11 01 04 [device_id]` pattern
- Offset +10: `02 XX` where XX appears related to device state or image count
- Examples: `02 03`, `02 04`, `02 05` (possible status codes 3-5)

**Status type 0x52 (Completion/Transition):**
- Indicates phase transition or completion event
- Followed by variable-length data payload (observed up to 100+ bytes)

### Image count tracking — CONFIRMED

The **5-byte subtype `02` packet** directly encodes image count at byte [2]:
- `16 02 01 02 02` → 1 image queued (initial state after first image was pre-loaded)
- Count is confirmed as byte [2] (0-indexed) of 5-byte subtype-02 notify packets
- Expect `02` at byte [2] when 2 images are loaded, `00` when queue is empty

### Battery level — CONFIRMED

The **5-byte subtype `01` packet** encodes battery at byte [3]:
- `16 01 00 03 44` → battery = 3 (full, confirmed by physical device display at test time)
- Range: 0-3 pips → values 0x00-0x03 at byte [3]

### Next steps

- Live test: connect, subscribe to 0x70954784 notify, read image count (expect 5-byte subtype 02 packet)
- Capture a controlled single-image send to observe count decrement (N → N-1)
- Decode the variable-length payload format in 0x52 completion messages
- Map payload structure in high-volume writes to 0x0020 (image data + metadata)

## Packet log format

```json
{
  "t": 1710000000.0,
  "direction": "phone_to_camera",
  "characteristic": "uuid",
  "data": "hex"
}
```

## Link-printer comparison

**Reference: [javl/InstaxBLE](https://github.com/javl/InstaxBLE) - Instax Mini/Square/Wide Link protocol**

### Similarities to javl/InstaxBLE (Mini Link):

| Aspect | InstaxBLE (Mini Link) | Our Evo Protocol | Notes |
|--------|---|---|---|
| **Service UUID** | `70954782-2d83-473d-9e5f-81e1d02d5273` | `70954782-2d83-...` (partial match) | Service prefix matches! Suggests vendor assigned range |
| **Write Characteristic** | `70954783-2d83-...` | Handle 0x0020 (0x1849 service) | Sequential UUIDs differ |
| **Notify Characteristic** | `70954784-2d83-...` | Handle 0x001D, 0x0027 (multiple) | Evo uses multiple notify channels |
| **Packet Header** | `41 62` ('Ab') | `1B` (opcode), then ATT payload | Different framing |
| **Image Chunk Size** | 900 bytes | ~100-200 bytes (observed) | Evo uses smaller chunks |
| **Status Messages** | EventType tuples (op1, op2) | `0xA8` prefix with sequence | Evo has custom status format |
| **Checksum** | `(255 - sum) & 0xFF` | Not observed in ATT stream | May be handled at ATT level |
| **Battery Level** | Query via EventType.SUPPORT_FUNCTION_INFO | Embedded in 0xA8 status messages | Evo broadcasts battery proactively |
| **Photos Left** | Queries printer | Tracked via image count field | Evo tracks queue state |

### Key Differences (Evo vs Link):

1. **Protocol Layer**
   - Link: Custom packet format over BLE writes/notifications
   - Evo: Direct ATT characteristic values (L2CAP CID 0x0004 ATT payloads)

2. **Service Architecture**
   - Link: Single write + notify pair for bidirectional communication
   - Evo: Asymmetric design with dedicated write (0x0020) and multiple notify channels (0x001D, 0x0027, 0x002A)

3. **Status Model**
   - Link: Request-response (query battery, photos, state)
   - Evo: Proactive notifications with encoded status, battery, image count in single `0xA8` messages

4. **Image Transfer**
   - Link: Explicit start/data/end sequence with chunk counting
   - Evo: Bulk write commands to 0x0020, status updates on 0x001D show progress

### Hypothesis: Protocol Evolution
Instax Evo may represent an evolution of the Link protocol:
- **Shared heritage**: Service UUID prefix match strongly suggests common vendor codebase
- **Simplified architecture**: Fewer orchestration packets, more direct data flow
- **Efficiency**: Smaller MTU might drive smaller chunks; multiple notify channels may support parallel status/data streams
- **Embedded intelligence**: Status broadcasting reduces request-response overhead (battery level already known without querying)

### Further Investigation Needed

- Decode `0xA8` status field correlation with Link's EventType structure
- Determine if 0x1849 service is a private/proprietary extension of Evo
- Capture a controlled Link printer session for direct protocol comparison
- Validate image chunk format and sequencing mechanism
