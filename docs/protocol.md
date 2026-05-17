
# Instax Evo BLE Protocol Notes

Analysis of the BLE protocol used by the Instax camera/printer family.
All findings derived from Android bugreport HCI captures cross-referenced with
[javl/InstaxBLE](https://github.com/javl/InstaxBLE).

---

## Protocol Coverage Status (as of 2026-05-17)

| Feature | Evo Wide FI028 (Gen 2) | Mini Evo FI019 (Gen 1) | Opcode(s) |
|---|---|---|---|
| BLE connect / handshake | ✅ | ✅ | `(00,00)` + `(00,01)` |
| Status poll (battery, photos left, model) | ✅ | ✅ | `(00,02)` |
| Transfer-ready flag detection | ✅ | ✅ (flag seen, transfer not usable) | `(00,02)` `CAMERA_FUNCTION_INFO` byte[2] |
| **Print** (phone → camera → film ejected) | ✅ | ✅ | `(80,xx)` print opcodes |
| Flash control | ✅ | ❓ Not tested | `(80,11)` reg_id=0x0b |
| Live view (pull loop) | ✅ | ⚠️ Partial — worked then failed; needs more investigation | `(82,00/01/02)` |
| Auto-transfer after shutter (inline) | ✅ seamless LV resume | ❓ Unknown — `(82,10/20/21/22)` untested on Gen 1 | `(82,10/20/21/22)` |
| Share-button image pull | ✅ | ❌ Camera disconnects on `(88,00)` | `(88,00…0b)` |
| History log / stored image list | ⏳ Not explored | ⏳ Not explored | `(84,xx)` |
| `(82,10/20/21/22)` via Share button | ❓ Only seen after shutter | ❓ Unknown | — |
| Register semantics (0x0C, 0x13–0x1B) | ❓ Unknown | ❓ Unknown | `(80,11)` read |
| `DEVICE_INFO` strings 0x03/0x04/0x05 | ❓ Unknown | ❓ Unknown | `(00,01)` InfoType 3–5 |
| `CAMERA_FUNCTION_INFO` byte[0]/byte[1] | ❓ Unknown | ❓ Unknown | values differ at rest vs. after print |
| Secondary GATT service (`0x6387…`) | ❓ Unknown | ❓ Unknown | possibly OTA / config |
| Gen 3 Cinema (FI0xx) | — | — | Not in possession; assumed same Link protocol |

---

## Key Insight: Two BLE Profiles, Two Protocols

Every Instax camera with BLE advertises **two separate BLE profiles** simultaneously:

| Profile | BLE address prefix | Protocol | Used by |
|---|---|---|---|
| **IOS** | `FA:AB:BC:xx:xx:xx` | Link protocol (`41 62` / `61 42` framing) | Instax iOS app, javl/InstaxBLE, **this project** |
| **Android** | `E0:48:24:xx:xx:xx` (Mini Evo) | Legacy binary (`16xx`/`17xx` writes) | Instax Android app only |
§
Both profiles share the **same GATT service and characteristic UUIDs** but speak entirely different application protocols.

> **javl/InstaxBLE only connects to `(IOS)` profiles** — its filter is `INSTAX-` prefix + `(IOS)` suffix.
> Our initial bugreport capture (17-34-32) used the **Android profile** — a red herring for the Link protocol.
> The correct target for all polling/printing code is the **IOS BLE profile**.

---

## Confirmed Camera Models

| Model | Model ID | Gen | BLE IOS address | BLE Android address | Film | Smartphone print px | Shots remaining |
|---|---|---|---|---|---|---|---|
| Instax Mini Evo | **FI019** | 1 | `FA:AB:BC:11:6F:D2` | `E0:48:24:D7:CF:2E` | instax mini | **600 × 800** (portrait) | 1 ✓ live |
| Instax Evo Wide | **FI028** | 2 | `FA:AB:BC:1D:0A:7B` | — | instax Wide | **1260 × 840** (landscape) | 4 ✓ HCI log |
| Instax Mini Evo Cinema | **unknown** | 3 | unknown (not captured) | — | instax mini | **800 × 600** (landscape cinema) | — |

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
| 0x80 | 0x11 | *(Evo-specific)* | Read/write individual register. **Read:** payload `[reg_id][0x00×5]`; camera replies `[0x00][reg_id][value][0x00×3]`. **Write:** payload `[reg_id][0x02][value][0x00×3]`; camera replies `[0x00][reg_id][0x00×4]`. See [Flash Control](#flash-control--set_info-0x8011) for reg_id=0x0B (flash mode). |
| 0x80 | 0x15 | `LIVE_VIEW_PREPARE` | Sent before `0x82,0x00`. Phone payload = 17×0x00. Camera response: Mini Evo = `[0xBF]` (1B); Wide Evo = 17B with byte[8]=0x32. |
| 0x82 | 0x00 | `LIVE_VIEW_START` | Payload = `[1B slot_index]`; camera ACKs with `[slot_index]`. |
| 0x82 | 0x01 | `LIVE_VIEW_FRAME` | Phone→cam: 0-byte payload (pull request). Cam→phone: `[2B chunk_idx=0x0001][3B frame_header][JPEG…]`. Each pull returns one **complete, fresh JPEG** of the current view (~20 fps). BLE fragmentation (bonded MTU 247): arrives as **5 ATT notifications** — 244+244+244+244+51 = 1027 bytes total. **JPEG starts at payload[5]** (after 2B chunk idx + 3B header). **Confirmed: 176 frames in 8.57 s (btsnoop), live view working on both FI019 and FI028 (2026-05-17).** |
| 0x82 | 0x02 | `LIVE_VIEW_END` | Payload = `[1B slot_index]`; camera ACKs with `[0x00]`. |
| 0x82 | 0x10 | `IMG_HIST_QUERY` | **Phone→cam:** `[0x00]` — initiates auto-transfer session after live view. **Cam→phone:** `[0x00]` — acknowledged. Sent immediately after the app acks the spontaneous `(82,02)` close (shutter fired); the camera has not yet finished encoding the photo at this point. |
| 0x82 | 0x20 | `IMG_HIST_POLL` | **Phone→cam:** empty payload — polls whether the image is ready. **Cam→phone:** `[0x02]` = not ready (retry); `[0x00][0x02][total_size:4B BE][chunk_size:4B BE]` = READY. Poll at ~500 ms intervals; camera takes 4–5 s to encode a fresh photo. |
| 0x82 | 0x21 | `IMG_HIST_CHUNK` | **Cam→phone (push):** `[chunk_idx:4B BE][jpeg_data…]` — camera pushes each chunk after the previous ACK. **Phone→cam (ack):** `[chunk_idx:4B BE]` — ACK for the received chunk. Camera pushes the next chunk after each ACK. |
| 0x82 | 0x22 | `IMG_HIST_END` | **Phone→cam:** empty payload — phone signals all chunks received. **Cam→phone:** `[0x00]` — done. |
| 0x84 | 0x00 | `CAMERA_LOG_SUBTOTAL_START` | Digital photo-to-phone transfer count query (**NOT** physical print count) — **confirmed gen 2** |
| 0x84 | 0x01 | `CAMERA_LOG_SUBTOTAL_DATA` | |
| 0x84 | 0x02 | `CAMERA_LOG_SUBTOTAL_CLEAR` | |
| 0x84 | 0x03 | `CAMERA_LOG_DATE_START` | |
| 0x84 | 0x06 | `CAMERA_LOG_FILTER_START` | |
| 0x88 | 0x00 | `IMAGE_TRANSFER_START` | **Phone→cam** (empty payload) — initiates the pull. Sent when `CAMERA_FUNCTION_INFO` transfer-ready flag (payload[4]) becomes `0x01`. **Cam→phone** ack: 5B `[00 00 00 00 00]` — camera is ready. |
| 0x88 | 0x01 | `IMAGE_TRANSFER_INFO` | **Phone→cam**: `[0x00 0x00 0x00 0x00]` (image index 0) — requests metadata. **Cam→phone**: 34B — total_size, chunk_data_sz, timestamp, img_count. See [Image Transfer section](#phone-initiated-image-transfer--088-pull-protocol-confirmed-gen-2). |
| 0x88 | 0x02 | `IMAGE_TRANSFER_DATA` | **Phone→cam**: `[chunk_idx: uint32 BE]` — requests chunk N. **Cam→phone**: single frame — `[img_idx:4][chunk_seq:1][jpeg_data…]`. No separate ack frame; the data frame IS the ack. |
| 0x88 | 0x03 | `IMAGE_TRANSFER_END` | **Phone→cam** (empty) — all chunks received. **Cam→phone**: 1B `[0x00]` (status OK). |
| 0x88 | 0x05 | `IMAGE_TRANSFER_RESULT` | **Phone→cam**: `[0x00 0x00 0x00 0x00]` — transfer complete. **Cam→phone**: 1B `[0x00]` (success). |
| 0x84 | 0x09 | `CAMERA_LOG_SLOT_QUERY` | Payload = `[1B slot_id]`. Response = `[2B slot_id_echo][4B data_size][4B data_size][4B record_count]` — e.g. slot=0 → 3 records, 4908B. Returns all-zeros if log is empty (Wide Evo with no prints) or after `CLEAR`. **Confirmed 2026-05-17.** |
| 0x84 | 0x0a | `CAMERA_LOG_SLOT_DATA` | Payload = `[1B slot_id][4×0x00]`. Camera immediately pushes back `[data_size]` bytes as a fragmented IOS Link packet. Format: `[6B header][N×RECORD]`. Slot 0: record_size=1636B, print_count at after_date[1] and [5]. Slot 2: record_size=1646B, filter counts at after_date[3,7,9,11,13,15]. **Confirmed 2026-05-17 + live 2026-06-17.** |
| 0x84 | 0x0b | `CAMERA_LOG_SLOT_ACK` | Payload = `[1B slot_id]`. Wide Evo replies `[0x00][slot_id]`. Acknowledges receipt of slot data. |

### InfoType payload values

The two polling commands use **different** InfoType numbering spaces:

#### `DEVICE_INFO_SERVICE` (op1=0x00, op2=0x01) — device identity strings

Response payload format: `[0x00][InfoType_echo][str_len][str_bytes…]`

| Value | Name | Wide Evo (FI028) example |
|---|---|---|
| 0x00 | `MANUFACTURER` | `"FUJIFILM"` (8 chars) |
| 0x01 | `MODEL_ID` | `"FI028"` (5 chars) |
| 0x02 | `SERIAL` | `"92007814"` (8 chars — matches BLE name suffix) |
| 0x03 | *(unknown string)* | `"0000"` |
| 0x04 | *(unknown string)* | `"0100"` |
| 0x05 | *(unknown string)* | `"0000"` |
| 0x09 | *(empty)* | `` |
| 0x0A | *(empty)* | `` |

#### `SUPPORT_FUNCTION_INFO` (op1=0x00, op2=0x02) — camera status

Response payload format: `[0x00][InfoType_echo][data…]`

| Value | Name | Notes |
|---|---|---|
| 0x00 | `IMAGE_SUPPORT_INFO` | **`[width: 2B BE][height: 2B BE][…]`** — always query this first; use the camera-reported size for all image prep. See [film dimensions table](#film-dimensions-by-model--print-mode). |
| 0x01 | `BATTERY_INFO` | `[battery_state][battery_pct]`. State: 0=critical, 1=low, 2=medium, 3=high, 4=full. |
| 0x02 | `PRINTER_FUNCTION_INFO` | `[status_byte][0x00][shots_in_pack: 2B]…`. `photos_left = status_byte & 0x0F`, `charging = bool(status_byte & 0x80)`. Wide Evo: status=0x26 → 6 remaining, shots_in_pack=0x000C=12. |
| 0x03 | `PRINT_HISTORY_INFO` | `[uint32 BE: transfers][uint32 BE: prints_made]`. Wide Evo: `00000004 00000005` → transfers=4 (digital photo-to-phone), prints_made=5 (physical film ejections). |
| 0x04 | `CAMERA_FUNCTION_INFO` | 16B data. **`data[2]` (= full payload[4]) = `0x01` when camera is in transfer-ready state** (user pressed Share); `0x00` at rest. This is the flag the app polls to know when to fire `(88,00)`. Confirmed from btsnoop: flag appeared 0.6 s before app sent `(88,00)`. Normal response (Wide Evo, new_capture): `03 50 00 00 00 00 00 00 00 05 04 01 00 00 00 00`; `data[0]`=0x03, `data[1]`=0x50. Semantics of other bytes TBD. |
| 0x05 | `CAMERA_HISTORY_INFO` | `[0x00][0x00][0x17]` (Wide Evo). `0x17`=23 — possibly lifetime print counter. |

### Film dimensions by model / print mode

The camera reports its authoritative print dimensions via `IMAGE_SUPPORT_INFO` (InfoType 0x00).
**Always query this first** and use the response — never hard-code dimensions by model name.

| Camera | Model ID | Film | `IMAGE_SUPPORT_INFO` (w×h) | Orientation | Chunk size | Resolution (smartphone) | Source |
|---|---|---|---|---|---|---|---|
| Instax Mini Evo | FI019 | instax mini | **600 × 800** | Portrait | 900 B | 318 dpi / 80 μm | Live capture ✓ |
| Instax Evo Wide | FI028 | instax Wide | **1260 × 840** | Landscape | 900 B | ~318 dpi | HCI log ✓ |
| Instax Mini Evo Cinema | unknown | instax mini | **800 × 600** | Landscape (cinema) | 900 B | 318 dpi / 80 μm | Fujifilm spec ✓ |
| Instax Mini Link / Square Link | — | mini / Square | 600×800 / 800×800 | Portrait / Square | 900 / 1808 B | — | javl/InstaxBLE |

**Dimension convention:** `IMAGE_SUPPORT_INFO` always returns `(width, height)` as two big-endian uint16.
A portrait 600×800 image means 600 px wide × 800 px tall; the Cinema's 800×600 is 800 wide × 600 tall (landscape).

**Cinema note:** The Mini Evo Cinema uses the same physical instax mini film cartridge as the original Mini Evo,
but prints in landscape orientation by rotating the print head direction. The native camera print mode
uses the full film strip (1600 × 600 dots), while smartphone print uses half (800 × 600). The camera will
report whichever dimension applies to the current print mode via `IMAGE_SUPPORT_INFO`.

**Chunk size:** All Mini/Wide cameras use 900 bytes per `PRINT_IMAGE_DOWNLOAD_DATA` chunk payload.
The only exception known is Square Link (1808 B), which uses a different film transport mechanism.

---

### Full session handshake flow (IOS profile, all models)

The complete sequence sent at the start of every session, before any print:

```
┌─ Connect ─────────────────────────────────────────────────────────────────────┐
│ Subscribe to notify char 70954784-2d83-473d-9e5f-81e1d02d5273               │
│ Write CCCD = 01 00  (enable notifications)                                   │
│ client.pair()  ← re-establish encrypted session (Gen 1 requires this)        │
└───────────────────────────────────────────────────────────────────────────────┘

SEND  op=(0x00,0x00)  payload=[]
  → SUPPORT_FUNCTION_AND_VERSION_INFO  ("hello" / session init)
RECV  op=(0x00,0x00)  payload=[version bytes]

SEND  op=(0x00,0x01)  payload=[0x00]
  → DEVICE_INFO_SERVICE  InfoType=MANUFACTURER  ("FUJIFILM")
RECV  op=(0x00,0x01)  payload=[0x00, 0x00, str_len, str_bytes...]
  parse: text = response[9:9+response[8]].decode('ascii')

SEND  op=(0x00,0x01)  payload=[0x01]
  → DEVICE_INFO_SERVICE  InfoType=MODEL_ID  ("FI019", "FI028", ...)
RECV  op=(0x00,0x01)  payload=[0x00, 0x01, str_len, str_bytes...]

SEND  op=(0x00,0x01)  payload=[0x02]
  → DEVICE_INFO_SERVICE  InfoType=SERIAL  (matches BLE name suffix)
RECV  op=(0x00,0x01)  payload=[0x00, 0x02, str_len, str_bytes...]

SEND  op=(0x00,0x02)  payload=[0x00]
  → SUPPORT_FUNCTION_INFO  InfoType=IMAGE_SUPPORT_INFO
RECV  op=(0x00,0x02)  payload=[0x00, 0x00, width_hi, width_lo, height_hi, height_lo, ...]
  parse: width, height = struct.unpack_from('>HH', response, 8)
  → determines film format; sets chunk_size from FILM_DIMS table

SEND  op=(0x00,0x02)  payload=[0x01]
  → SUPPORT_FUNCTION_INFO  InfoType=BATTERY_INFO
RECV  op=(0x00,0x02)  payload=[0x00, 0x01, battery_state, battery_pct]
  parse: state = response[8]   # 0=critical 1=low 2=medium 3=high 4=full
         pct   = response[9]   # 0–100

SEND  op=(0x00,0x02)  payload=[0x02]
  → SUPPORT_FUNCTION_INFO  InfoType=PRINTER_FUNCTION_INFO
RECV  op=(0x00,0x02)  payload=[0x00, 0x02, status_byte]
  parse: photos_left = response[8] & 0x0F   # low 4 bits
         is_charging  = bool(response[8] & 0x80)
```

Response byte layout (all DEVICE_INFO_SERVICE and SUPPORT_FUNCTION_INFO responses):

```
Byte:  0    1    2    3    4    5    6    7    8    9 …  last
       61   42  [len_hi len_lo]  op1  op2  00  InfoType  [data...]  cs
                                                ^^^^^^^^^
                                                2-byte prefix before actual data
```

Actual data always starts at **`response[8]`** (offset 8 from start of packet).

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
# Always use the camera-reported (width, height) for image preparation.
# Known values (smartphone print mode):
#   Gen 1 Mini Evo (FI019):   600 × 800  (portrait)
#   Gen 2 Evo Wide (FI028): 1260 × 840  (landscape)
#   Gen 3 Cinema (unknown):   800 × 600  (landscape — confirmed from Fujifilm spec)
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
All behaviours below have been confirmed by **live testing** on `FA:AB:BC:1D:0A:7B` (2026-05-17).

### Connection sequence

```
Write h=0x0013 = 01 00                Enable CCCD for notify char h=0x0012

op=(0x00,0x00)  []                    SUPPORT_FUNCTION_AND_VERSION_INFO
op=(0x00,0x01)  [0x00]                → "FUJIFILM"  (MANUFACTURER)
op=(0x00,0x01)  [0x01]                → "FI028"     (MODEL_ID)
op=(0x00,0x01)  [0x02]                → "92007814"  (SERIAL)
op=(0x00,0x01)  [0x03]                → "0000"      (unknown)
op=(0x00,0x01)  [0x04]                → "0100"      (unknown)
op=(0x00,0x01)  [0x05]                → "0000"      (unknown)
op=(0x00,0x01)  [0x09]                → (empty)
op=(0x00,0x01)  [0x0A]                → (empty)
op=(0x00,0x02)  [0x00]                IMAGE_SUPPORT_INFO → 1260×840 (Wide confirmed) ✓
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
op=(0x84,0x00)  []                    CAMERA_LOG_SUBTOTAL_START → digital transfers to phone = 4
op=(0x84,0x01)  [00 00 00 00]         CAMERA_LOG_SUBTOTAL_DATA (no response observed)
op=(0x84,0x02)  []                    CAMERA_LOG_SUBTOTAL_CLEAR → ACK 0x00
op=(0x84,0x09)  [0x00]               HISTORY_ENTRY_QUERY index=0 → 14 bytes (all zeros)
op=(0x84,0x0b)  [0x00]               HISTORY_ENTRY_ACK   index=0 → [0x00][0x00]
op=(0x84,0x09)  [0x02]               HISTORY_ENTRY_QUERY index=2 → [0x00,0x02, 12×0x00]
op=(0x84,0x0b)  [0x02]               HISTORY_ENTRY_ACK   index=2 → [0x00][0x02]
# [history download: 0x80,0x15 + 0x82,0x00 + 176× 0x82,0x01 + 0x82,0x02]
[app polls SUPPORT_FUNCTION_INFO InfoTypes 04,05,02,03,01 in rotation every ~0.5s]
```

### Digital photo transfers — Evo Wide

`CAMERA_LOG_SUBTOTAL_START` (op=0x84,0x00) response payload (bytes 6–17):
```
00 00 00 00  04 00 00 00  04 00 00 00  00
             ^^^^^^^^^^^  ^^^^^^^^^^^
             uint32 LE=4  uint32 LE=4  ← both fields = digital photo transfers to phone (NOT physical prints!)
```
In the 19-51-52 capture: 4 total transfers (3 phone-initiated, 1 camera-initiated), 0 physical prints made.
Use `SUPPORT_FUNCTION_INFO InfoType=0x02` (`PRINTER_FUNCTION_INFO`) for shots remaining.

### Image size — Evo Wide

`SUPPORT_FUNCTION_INFO` IMAGE_SUPPORT_INFO (0x00,0x02 InfoType=0x00) response payload [16B total]:
```
00 00  04 EC  03 48  02  0B  00 0A  50 00  01 00 00 00
^^^^^  ^^^^^  ^^^^^  
 prefix w=1260 h=840  (remaining fields TBD — possibly chunk params, flags)
```
Width=0x04EC=1260, Height=0x0348=840 ✓ Instax Wide film dimensions.

### Keepalive poll cycle — Evo Wide

The app continuously polls `SUPPORT_FUNCTION_INFO` InfoTypes in a round-robin while idle:
```
→ 0x00,0x02 InfoType=0x04  (CAMERA_FUNCTION_INFO)  ← cam: 02 32 00×14 [18B]
→ 0x00,0x02 InfoType=0x05  (CAMERA_HISTORY_INFO)   ← cam: 00 00 17    [6B]
→ 0x00,0x02 InfoType=0x02  (PRINTER_FUNCTION_INFO) ← cam: 26 00 00 0c 00×4 [10B]
→ 0x00,0x02 InfoType=0x03  (PRINT_HISTORY_INFO)    ← cam: 00 00 00 04 00 00 00 05 [10B]
→ 0x00,0x02 InfoType=0x01  (BATTERY_INFO)          ← cam: 02 32 00 00 [6B]
Repeat every ~0.5 s
```

Decoded keepalive values from 19-51-52 capture:
- Battery: state=2 (medium), pct=50%
- Shots remaining: `0x26 & 0x0F` = 6; shots_in_pack=12 (Wide Evo)
- Digital photo transfers to phone: 4 (PRINT_HISTORY_INFO field 1 = 4); physical film ejections: 5 (field 2 = 5, `prints_made`)
- CAMERA_FUNCTION_INFO byte[0]=0x02, byte[1]=0x32=50 (semantics TBD)
- CAMERA_HISTORY_INFO byte[2]=0x17=23 (possibly lifetime print counter)

---

## Flash Control — SET_INFO (0x80,0x11), reg_id=0x0B (confirmed gen 2, bugreport 0517b)

The Wide Evo (FI028) exposes a flash mode register via the `SET_INFO` op `(0x80,0x11)`. The phone reads the current setting at startup and writes it back when the user changes the flash toggle in the app.

### Register access format

```
READ  phone→cam: (0x80,0x11)  payload=[reg_id][0x00×5]
      cam→phone: (0x80,0x11)  payload=[0x00][reg_id][current_value][0x00×3]

WRITE phone→cam: (0x80,0x11)  payload=[reg_id][0x02][new_value][0x00×3]
      cam→phone: (0x80,0x11)  payload=[0x00][reg_id][0x00×4]   (ACK — does not echo new value)
```

### Flash mode (reg_id=0x0B)

| `new_value` | Flash setting |
|---|---|
| `0x00` | AUTO |
| `0x01` | ON (forced flash) |
| `0x02` | OFF (no flash) |

**Startup read:** Phone sends `[0x0b 0x00 0x00 0x00 0x00 0x00]`; camera replies with `[0x00 0x0b <current> 0x00 0x00 0x00]`.

**Example writes from bugreport 0517b:**
```
Flash OFF:  phone→cam (0x80,0x11) payload=0b 02 02 00 00 00
Flash ON:   phone→cam (0x80,0x11) payload=0b 02 01 00 00 00
Flash AUTO: phone→cam (0x80,0x11) payload=0b 02 00 00 00 00
```

All three flash changes happened during an ongoing live view session — the BLE connection stays up and the live view session does not need to be interrupted to change flash.

### Other known registers (startup read-only, semantics TBD)

| reg_id | startup value (Wide Evo) | Notes |
|---|---|---|
| 0x0B | 0x00 | Flash mode (AUTO default) — **confirmed** |
| 0x0C | 0x00 | Unknown |
| 0x13 | 0x00 | Unknown |
| 0x14 | 0x00 | Unknown |
| 0x15 | 0x00 | Unknown |
| 0x16 | 0x32 = 50 | Possibly total prints (matches `CAMERA_FUNCTION_INFO` byte[1]) |
| 0x17 | 0x01 | Unknown |
| 0x18–0x1A | 0x00 | Unknown |
| 0x1B | 0x01 | Unknown |

---

## Phone-Initiated Image Transfer — 0x88 Pull Protocol (confirmed gen 2)

When the user presses the **share button** on the camera, a flag in the
`CAMERA_FUNCTION_INFO` keepalive poll response changes (see below). The **phone detects
this flag and initiates every step of the transfer** — the camera never pushes
unsolicited image data. The phone pulls the image chunk by chunk.

> **Correction from earlier notes:** This was initially described as camera-initiated push.
> Analysis of the btsnoop (`_diff_status_before_88.py`, 2026-05-17) confirmed it is
> phone-initiated pull: the phone sends `(88,00)` first, 0.6 s after the flag appears.

### Transfer-ready detection

The app continuously polls `CAMERA_FUNCTION_INFO` (op=`0x00,0x02` InfoType=`0x04`) as
part of its idle keepalive loop. When the user presses Share on the camera:

```
Normal response payload[4] = 0x00:
  00 04 03 50 [00] 00 00 00 00 00 00 05 04 01 00 00 00 00

Transfer-ready response payload[4] = 0x01 or 0x02:
  00 04 03 50 [01] 00 00 00 00 00 05 05 00 00 00 00 00 00  ← ready / standby
  00 04 03 50 [02] 00 00 00 00 00 05 05 00 00 00 00 00 00  ← actively transferring
                ^^
  data[2] / payload[4] flips 0x00 → 0x01 when camera enters transfer mode.
  Value 0x02 also occurs (persists after an initial 0x01 state or on fresh press).
  Fire (88,00) on ANY non-zero value.
```

Live observation (Wide Evo, 2026-05-17):
- Flag starts at `0x01` (persists between BLE sessions until camera is power-cycled)
- Transitions to `0x02` when user presses Transfer on a camera that was already at `0x01`
- Both values accepted: send `(88,00)` on `payload[4] != 0x00`

### Queue behaviour (confirmed 2026-05-17)

The camera maintains an internal queue of images shared by the user. Each successful
`(88,05)` dequeues one image. The flag **stays non-zero as long as images remain in
the queue** and drops to `0x00` only when the queue is empty. There is **no queue
depth field** exposed in the protocol:

- `img_count` in `(88,01)` metadata — always `1` regardless of queue depth
- `(88,00)` ack `[00 00 00 00 00]` — always all-zero
- `CAMERA_FUNCTION_INFO` payload bytes `[9:11]` — always `0x00 0x00` while images are queued
- `CAMERA_FUNCTION_INFO` payload bytes `[11:12]` — always `0x05 0x05` (constant, unrelated)

**Drain algorithm:** loop back to polling after each successful pull; exit when `flag == 0x00`.

Duplicate shares: if the user presses Share on the same photo twice, it enters the
queue twice and is transferred twice as separate entries.

Confirmed from btsnoop (new_capture, Wide Evo, 2026-05-17):
flag appeared at T+2616.183s, `(88,00)` sent at T+2616.769s — 0.6 s later.

Confirmed end-to-end in live Python/bleak run (Wide Evo, 2026-05-17):
4 images drained automatically in one BLE session (221,773 B / 235,414 B / 235,414 B / 210,761 B).
Per-image BLE time: ~12–19 s. Camera SD read is the bottleneck (~0.7 s/chunk).

### Transfer sequence (phone-initiated pull)

```
# ── Trigger detection ───────────────────────────────────────────────────────
[poll loop]  phone → cam: op=(0x00,0x02) InfoType=0x04  (CAMERA_FUNCTION_INFO)
             cam → phone: payload[4] = 0x00  (not ready — keep polling)
             … user presses Share on camera …
             cam → phone: payload[4] != 0x00  ← TRANSFER READY (0x01 or 0x02)

# ── Pull sequence ──────────────────────────────────────────────────────────
phone → cam: op=(0x88,0x00)  payload=[]                     # start pull request
cam → phone: op=(0x88,0x00)  5B  [00 00 00 00 00]           # camera ready ack

phone → cam: op=(0x88,0x01)  payload=[0x00 0x00 0x00 0x00]  # request metadata (img index 0)
cam → phone: op=(0x88,0x01)  34B  metadata                  # total_size, chunk_data_sz, timestamp, count

for chunk_idx in range(num_chunks):
    phone → cam: op=(0x88,0x02)  payload=[chunk_idx: uint32 BE]        # request chunk N
    cam → phone: op=(0x88,0x02)  [img_idx:4][chunk_seq:1][jpeg_data…]  # chunk data (one frame only)

phone → cam: op=(0x88,0x03)  payload=[]                     # all chunks received
cam → phone: op=(0x88,0x03)  1B  [0x00]                    # status OK

phone → cam: op=(0x88,0x05)  payload=[0x00 0x00 0x00 0x00]  # transfer complete
cam → phone: op=(0x88,0x05)  1B  [0x00]                    # done
```

> **Correction from btsnoop analysis:** The btsnoop-derived protocol showed two camera frames
> per chunk (`[00 00 00 00]` ack + separate data frame). This was a misparse. In reality the
> camera sends **exactly one (88,02) frame per chunk**. The `[img_idx:4]` header at the start
> of the chunk payload was being mistaken for a separate 4-byte ack packet. Confirmed by
> live Python/bleak testing (2026-05-17): expecting two frames causes a 30s timeout on the
> second recv; dropping to one frame completes the full transfer.

From btsnoop timestamps (new_capture, T offset from session start):
```
T+2616.183s  CAMERA_FUNCTION_INFO transfer-ready flag appears
T+2616.769s  phone → (88,00)  start
T+2616.800s  cam   → (88,00)  ready [00 00 00 00 00]
T+2617.0s    phone → (88,01)  metadata request
T+2617.0s    cam   → (88,01)  34B metadata
T+2617.2s    phone → (88,02)  chunk 0
…            (23 chunks × ~200 ms each)
T+2621.5s    phone → (88,03)  end
T+2621.5s    cam   → (88,03)  [00]
T+2621.5s    phone → (88,05)  complete
T+2621.5s    cam   → (88,05)  [00]
```

### 0x88,0x01 metadata layout (34 bytes)

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

### 0x88,0x02 chunk layout

Each IOS Link frame with op=(0x88,0x02) carries:
```
[img_idx : uint32 BE]  — image index within transfer (0x00000000 for first/only image)
[chunk_seq: uint8]     — 0-based chunk sequence number (0x00, 0x01, … 0x16 for 23 chunks)
[jpeg_data: N bytes]   — chunk_data_size bytes of raw JPEG data (9,749B for all but last)
```

To reconstruct the JPEG:
```python
image_bytes = bytearray()
for chunk_payload in all_0x88_02_payloads:
    image_bytes.extend(chunk_payload[5:])   # skip 5-byte header
# image_bytes now contains the complete JPEG (starts with FF D8 FF)
```

### Confirmed transfer examples (2026-05-17, live Python/bleak, Wide Evo)

All 4 images drained in a single BLE connection using the polling loop:

| # | Total bytes | Chunks | BLE time | Image timestamp (camera clock) | Note |
|---|---|---|---|---|---|
| 1 | 221,773 | 23 (22×9749 + 7295) | ~16 s | 2026-06-17 12:14:52 | — |
| 2 | 235,414 | 25 (24×9749 + 1438) | ~19 s | 2026-03-24 16:17:35 | — |
| 3 | 235,414 | 25 (24×9749 + 1438) | ~16 s | 2026-03-24 16:17:35 | duplicate share |
| 4 | 210,761 | 22 (21×9749 + 6032) | ~12 s | 2026-01-24 20:45:20 | — |

BLE MTU = 247 (bonded; `pair()` required before `start_notify()`). Script: `scripts/image_receive.py`.

### Image selection

The `(88,01)` request payload `[img_idx: uint32 BE]` selects which image to transfer.
Always send `[0x00 0x00 0x00 0x00]` for the first (and usually only) image.

The camera returns `img_count` in the metadata. **`img_count` is always `1`** regardless
of how many images are queued — it does not reflect the queue depth. The camera
exposes no queue count anywhere in the protocol; the only queue signal is the
transfer-ready flag (non-zero = more images remain).

**Index out of range:** Requesting a non-existent index (e.g. `[0x00 0x00 0x00 0x02]`
when `img_count = 1`) causes the camera to reply to `(88,01)` with a 1-byte error
response `[0x81]`. Bit 7 set = error; low bits = error code. The camera still accepts
the next `(88,00)` cleanly — it does not lock up. **To transfer a different image,
select it on the camera and press Share/Transfer again** — a new session begins with a
fresh `img_count` and the chosen image at index 0.

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

---

## Live View / History Download — 0x82 Pull Protocol

The `(82,xx)` opcodes are used for two related but distinct operations:

| Context | What's sent | Each `(82,01)` returns |
|---|---|---|
| **Live view** | Slot 0, no preceding `(84,xx)` | One complete small JPEG (~1 KB) of current camera view |
| **History download** | Slot = history index, preceded by `(84,09)/(84,0a)/(84,0b)` | One chunk of a large stored JPEG |

Both use identical on-wire framing. The preceding `(84,xx)` setup (or lack thereof) tells the
camera which mode to use. **Do not close and reopen the session between pulls** — keep one
`(82,00)` session open for the entire image/sequence.

### Confirmed session timing (handle_split.txt — Mini Evo session 156)

From a decoded Windows HCI log (`captures/handle_split.txt`, UTF-16 LE):

```
t=125.73 s   phone → cam: op=(0x80,0x15)  payload=[17×0x00]   LIVE_VIEW_PREPARE
t=125.82 s   phone → cam: op=(0x82,0x00)  payload=[0x00]      open session, slot=0
t=125.82 s   cam → phone: op=(0x82,0x00)  [0x00]              ACK (echo slot)
t=125.87 s   phone → cam: op=(0x82,0x01)  []                  first pull
t=125.87 s   cam → phone: op=(0x82,0x01)  [2B][3B][JPEG…]    first response
…            175 × (82,01) at ~50 ms intervals …
t=134.38 s   phone → cam: op=(0x82,0x02)  payload=[0x00]      close session (sent TWICE)
t=134.38 s   cam → phone: op=(0x82,0x02)  [0x00]              ACK

Total session:  8.56 s
Pull count:     175 × (82,01) @ ~50 ms cadence
Cam→phone:      1600 packets, 199,990 bytes, 176 JPEG sigs
```

Key observations:
- **One JPEG per pull**: 175 pulls → 176 JPEG SOI markers means each `(82,01)` response
  carries one complete small JPEG (≈1136 bytes average at 160×106 px, low quality).
- **Session stays open**: `(82,00)` is sent once; `(82,02)` is sent only when the app
  is done — **not** between frames. Opening a new session per frame would add ~8.5 s overhead.
- **`(82,02)` sent twice**: the real app sends the end command twice in succession; the
  camera responds to both. Sending twice is safe and matches observed behaviour.
- **Pull cadence**: 50 ms between sends (20 fps). Implement with a ~50 ms notification
  drain window after each pull; total per-frame latency ≈ 50–100 ms.

### Full live view sequence

```
phone → cam: op=(0x80,0x15)  payload=[17×0x00]   prepare
cam → phone: op=(0x80,0x15)  [response]           ACK (Mini Evo: 1B 0xBF; Wide: 17B)

# Flush any stale notifications from _rx before opening
phone → cam: op=(0x82,0x00)  payload=[0x00]       open session (slot 0)
cam → phone: op=(0x82,0x00)  [0x00]               ACK

loop until user stops or camera sends (82,02):
    phone → cam: op=(0x82,0x01)  payload=[]                           pull request
    cam → phone: op=(0x82,0x01)  [2B chunk_idx][3B header][JPEG…]    response
        # Use _recv_frame() — accumulates 5 ATT notifications (244×4+51) into 1027-byte frame
        # JPEG data starts at payload[5] — skip 2B chunk_idx + 3B header field
        # After emitting frame: drain _rx for a spontaneous (82,02) without blocking

    if (82,02) received (shutter fired, frame_count > 0):
        # Acknowledge, run the chunk transfer inline, then reopen — seamlessly.
        phone → cam: op=(0x82,0x02)  payload=[0x00]   # ack the close
        << run IMG_HIST_QUERY / IMG_HIST_POLL / chunk loop / IMG_HIST_END >>
        sleep(2.0)                                    # camera recovery time
        phone → cam: op=(0x82,0x00)  payload=[0x00]   # reopen pull session
        cam → phone: op=(0x82,0x00)  [0x00]           # ACK
        # reset frame_count → 0 and continue pulling

    if (82,02) received (no frames yet):
        phone → cam: op=(0x82,0x02)  payload=[0x00]   # close
        cam → phone: op=(0x82,0x02)  [0x00]           # ACK
        # exit inner loop, outer loop retries session after 2 s
```

> **Inline transfer after shutter (confirmed 2026-05-17):** When the camera fires the
> shutter during live view it sends a spontaneous `(82,02)` close. Instead of exiting
> the session management loop entirely, the correct approach is to handle the transfer
> *inside* the inner pull loop: ack the close → run the `(82,10/20/21/22)` transfer
> → sleep ~2 s for camera recovery → reopen with `(82,00)` → continue pulling frames.
> The user sees no "session stopped / starting" interruption.
> A non-blocking drain of the notify queue after each frame is still needed to catch
> a `(82,02)` that arrived while the previous frame was being emitted.

### `(82,01)` response layout

Each `(82,01)` response is one IOS Link frame spanning **5 BLE ATT notifications**
(confirmed from btsnoop, bonded MTU = 247 → 244 bytes usable per notification):

```
Notification 1 (244 B):  61 42 [04 03]  82 01  [payload bytes 0–237]     ← IOS Link header + start of payload
Notification 2 (244 B):  [payload bytes 238–481]                          ← raw continuation
Notification 3 (244 B):  [payload bytes 482–725]                          ← raw continuation
Notification 4 (244 B):  [payload bytes 726–969]                          ← raw continuation
Notification 5 ( 51 B):  [payload bytes 970–1019]  [cs]                   ← tail + checksum

Total IOS Link frame: 1027 bytes  (len field = 0x0403)
Payload (frame[6:1026]): 1020 bytes
  payload[0:2]  = chunk index, always 0x00 0x01
  payload[2:5]  = 3-byte frame header field (e.g. 0x00 0x03 0xF7 — varies per frame)
  payload[5:]   = complete JPEG image (SOI 0xFF 0xD8 … EOI 0xFF 0xD9)
                  typical size ~1000 bytes at 160×106 px, low quality
```

Confirmed from btsnoop decode (`captures/handle_split.txt`, session 156 — Mini Evo):
`payload[0:8]` = `00 01  00 03 F7  FF D8 FF` — chunk idx, header, SOI.

### Reassembling the frame in bleak

In bleak all ATT notifications arrive via the same notify callback — both the IOS Link
framed first notification and the 4 raw continuation bytes. Use `_recv_frame()` (which
accumulates `_rx` bytes into `buf` until `len(buf) >= total`) rather than manually
dragging a time-based drain window:

```python
op1, op2, payload = await _recv_frame(timeout=5.0)
# payload[5:] is the complete JPEG
soi = payload.find(b'\xff\xd8', 5)
eoi = payload.rfind(b'\xff\xd9')
if soi >= 0 and eoi > soi:
    frame = payload[soi:eoi + 2]
```

**Do not** use a 50 ms time-based drain window — it is unreliable: it may fire before
all 5 notifications arrive (giving a truncated JPEG) or a spontaneous `(82,02)` may
arrive after the window closes and corrupt the next pull's `_recv_frame` call.

---

## Post-Photo Auto-Transfer — 0x82 History Protocol (confirmed gen 2, bugreport 0517b)

When the user takes a photo via remote shutter (phone app shutter button during live view), the camera automatically encodes the JPEG and makes it available for transfer via the `(0x82,0x10/0x20/0x21/0x22)` opcode family. This is **distinct from the `(0x88,xx)` share-button pull** — the camera pushes chunks once it signals readiness.

**Three photos** were transferred in bugreport 0517b (Wide Evo FI028, 2026-05-17): flash ON, flash OFF, flash AUTO. Sizes: 216,035 B, 216,968 B, 213,221 B. Each image required ~22–23 chunks at 9,749 B/chunk.

### Transfer sequence

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

# ── Chunk transfer (REQUEST-RESPONSE — confirmed from btsnoop capture 0517b) ─
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

> **Same direction as 0x88:** Both protocols use phone-initiated chunk requests, NOT camera push.
> The earlier analysis that said "camera pushes" was wrong; corrected 2026-05-17 from live capture.

### Timing (from bugreport 0517b)

| Event | Offset |
|---|---|
| `LIVE_VIEW_END` (last frame) | T+0 |
| `IMG_HIST_QUERY` (0x82,0x10) | T+0 ms |
| First `IMG_HIST_POLL` not-ready | T+80 ms |
| Last `IMG_HIST_POLL` not-ready | T+4,550 ms |
| `IMG_HIST_POLL` READY | T+4,600 ms |
| First chunk pushed | T+4,796 ms |
| Last chunk pushed / `IMG_HIST_END` | T+9,200 ms |

Total per-image time (encode + transfer): ~9 seconds for a 216 KB JPEG.

### Python receive skeleton

```python
import struct, math

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
        await asyncio.sleep(0.5)   # not ready
    else:
        return None  # timed out

    # 3. Request each chunk (phone requests, camera responds)
    # Response payload: [status:1B][chunk_idx:4B BE][jpeg_data…]
    jpeg = bytearray()
    num_chunks = -(-total_size // chunk_size)  # ceiling division
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

### When to trigger

Triggered by a spontaneous `(82,02)` close from the camera during live view (shutter fired). Send `IMG_HIST_QUERY` **immediately after acknowledging the `(82,02)` close** and before reopening the live view session with `(82,00)`. The camera takes ~4–5 s to encode the JPEG after the shutter fires, so poll `(82,20)` at ~500 ms intervals. If the camera has no image ready it keeps returning `[0x02]` — use a timeout (e.g. 30 s / 60 polls) to give up gracefully.

This protocol was only observed after **remote shutter captures** (phone app shutter button during live view). Whether it is triggered by the Share button (like `(0x88,xx)`) is not yet confirmed.

---

## Gen 1 (FI019 Mini Evo) — Protocol Compatibility Notes

The Mini Evo (Gen 1, model FI019, firmware-updated) participates in the IOS Link
protocol for status queries and printing, but **does not support the `(88,xx)` image
transfer protocol** and has only partial live view support.

### Confirmed behaviour (live tests, Mini Evo `FA:AB:BC:11:6F:D2`)

| Feature | Status | Notes |
|---|---|---|
| Status queries (00,xx) | ✅ Works | Battery, model, serial, photos_left all returned correctly |
| `CAMERA_FUNCTION_INFO` poll | ✅ Works | Flag appears (0x01) when user presses Transfer |
| **Print** (phone → camera → film ejected) | ✅ Works | Same `(80,xx)` print sequence as Gen 2 |
| `(88,00)` IMAGE_TRANSFER_START | ❌ **Camera disconnects** | Sending `(88,00)` causes the camera to drop the BLE link immediately |
| Live view `(82,xx)` | ⚠️ **Partial** | Frames received in initial tests (same `(82,00/01/02)` framing as Gen 2) but subsequently failed to maintain a stable session. Root cause unknown — may be a timing, pairing, or firmware issue. Needs further investigation. |
| Auto-transfer after shutter `(82,10/20/21/22)` | ❓ Not tested | Unknown whether Gen 1 supports this after a live-view shutter |
| `(84,xx)` log queries | ⏳ Not explored | — |

### `(88,xx)` not supported on Gen 1

When the Mini Evo shows `CAMERA_FUNCTION_INFO` flag = `0x01` and `(88,00)` is sent:
- Camera sends a BLE disconnect event with no error response
- No `(88,00)` ACK is ever sent
- Subsequent reconnect succeeds, but flag may still be `0x01` — **do not retry `(88,00)`**

**Detection strategy:** time out on the `(88,00)` response with a short timeout (e.g. 5 s).
If no response, set a `_transfer_supported = False` flag and skip all further `(88,xx)`
attempts in the current session. The camera remains usable for live view and status polling.

The Gen 1 camera presumably uses a different mechanism to transfer images to a phone
(possibly via the camera's Wi-Fi or a separate app flow not captured in these sessions).
The `(88,xx)` opcodes may be Gen 2+ only.

---

## Windows / bleak Implementation Notes

These quirks apply when using the [bleak](https://github.com/hbldh/bleak) library on
Windows (WinRT BLE backend). They do **not** affect macOS/Linux CoreBluetooth/BlueZ.

### MTU and bonding

| MTU at connect | Meaning | Action required |
|---|---|---|
| 247 | Device is bonded (paired) | None — `start_notify` will succeed |
| 23 (default) | Not bonded | Call `client.pair()` before `start_notify()` |

Gen 1 cameras require pairing even on reconnect. After `pair()`, sleep ≥ 3 s before
calling `start_notify()` — the GATT cache on Windows may not yet be populated.

### Post-disconnect GATT "Characteristic not found"

After a camera-initiated disconnect (e.g. the camera drops BLE due to an unknown command),
Windows re-uses the stale GATT service cache from the previous connection. A new
`BleakClient` created from a `BLEDevice` object (returned by a fresh scan) inherits
this cache and fails with:

```
BleakError: Characteristic 70954784-2d83-473d-9e5f-81e1d02d5273 was not found!
```

**Fix:** Create `BleakClient` from the **address string**, not from the `BLEDevice` object:

```python
# ❌ Do NOT do this — reuses cached GATT table
dev = await BleakScanner.find_device_by_address(address)
client = BleakClient(dev, timeout=30)

# ✅ Do this — forces fresh service discovery
client = BleakClient(address, timeout=30)
```

### Reliable subscribe sequence (Gen 1)

```python
await client.connect()
await client.pair()
await asyncio.sleep(3.0)   # wait for GATT cache to populate

# Retry start_notify up to 3 times with 2 s delay
for attempt in range(1, 4):
    try:
        await client.start_notify(NOTIFY_UUID, callback)
        break
    except Exception as e:
        if attempt == 3:
            raise
        await asyncio.sleep(2.0)
```

### Reconnect after camera-initiated disconnect

After detecting connection loss (notify callback stops arriving or bleak raises):

1. Call `await client.disconnect()` and set `client = None` (clears stale state)
2. Wait ≥ 5 s (camera BLE stack needs time to re-advertise after self-disconnect)
3. Scan for the device by address string and create a **new** `BleakClient(address, ...)`
4. Run the reliable subscribe sequence above

If the camera disconnected because of an unsupported command (e.g. `(88,00)` on Gen 1),
set a flag to skip that command on reconnect — otherwise the cycle repeats.
Sequence counter is global across BLE connection sessions.

---

## Known Film Counts (confirmed)

| Camera | Film remaining | Source |
|---|---|---|
| Gen 1 Mini Evo (FI019) | 1 shot | `PRINTER_FUNCTION_INFO` response[8] & 0x0F = 1 ✓ live |
| Gen 1 Mini Evo (FI019) | 1 shot | Android protocol `16 02` response byte[2] (cross-check) |
| Gen 2 Evo Wide (FI028) | 6 shots | `PRINTER_FUNCTION_INFO` status=0x26, 0x26 & 0x0F = 6 (HCI log, keepalive) |

> `CAMERA_LOG_SUBTOTAL_START` (op=0x84,0x00) returns **digital photo transfers to phone** (two uint32 LE fields), **NOT** physical print count and NOT shots remaining.
> Wide Evo: both fields = 4 (4 digital transfers: 3 phone-initiated + 1 camera-initiated; 0 physical prints made in session).
> Use `PRINTER_FUNCTION_INFO` (InfoType=0x02) for shots remaining.

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

### Wide Evo — BLE Profile Differences (confirmed 2026-05-17)

The Wide Evo (FI028, `FA:AB:BC:1D:0A:7B`) uses the same IOS Link protocol but has several
connection-level differences from the Mini Evo:

**Advertising name:** `INSTAX-[serial](BLE)` — note the `(BLE)` suffix, **not** `(IOS)`.
Despite this, the camera uses the same IOS Link service UUID and identical protocol framing.
The `(BLE)` label appears to be a firmware artifact, not an indicator of the Android profile.

**Pairing required every session:** Wide Evo does not retain bond state across connections
the way Mini Evo does. Call `await client.pair()` after connecting and before writing CCCD.
The pairing completes with a simple PIN confirmation dialog (no real PIN — just click through).
`pair()` may raise `"OPERATION_ALREADY_IN_PROGRESS"` on retries; treat as non-fatal and
wait ~3 s before proceeding.

**Windows interference quirk:** If the Mini Evo (`FA:AB:BC:11:6F:D2`) is listed in Windows
Bluetooth devices, its bond record interferes with Wide Evo pairing and causes repeated
`pair()` failures. **Remove the Mini Evo from Windows Bluetooth settings** before pairing
the Wide Evo.

**GATT handles:** Write h=0x0010, Notify h=0x0012, CCCD h=0x0013 (differs from Mini Evo's
h=0x0014/0x0016). MTU negotiates to 247 bytes.

**LIVE_VIEW_PREPARE (0x80,0x15) response:** Wide Evo returns 17 bytes:
`[8×0x00][0x32][0x01][7×0x00]` — byte[8] = `0x32` = 50 (meaning TBD; matches
`CAMERA_FUNCTION_INFO` byte[1]). Mini Evo returns 1 byte `[0xBF]`.

**LIVE_VIEW_FRAME delivery:** Wide Evo delivers the full JPEG in a single ATT burst
(one `0x82,0x01` pull) rather than requiring 2 pulls like the Mini Evo.

```python
# Wide Evo connect pattern:
dev = await BleakScanner.find_device_by_filter(
    lambda d, a: d.address.upper() == "FA:AB:BC:1D:0A:7B", timeout=30
)
client = BleakClient(dev, timeout=30)
await client.connect()
try:
    await client.pair()
except Exception:
    pass                              # non-fatal; camera may already be pairing
await asyncio.sleep(3.0)             # settle after pair before subscribing
await client.start_notify(NOTIFY_UUID, handler)
```

### Mini Evo — Transfer Mode BLE Quirks (confirmed 2026-05-17)

When the user presses the camera's share button, the Mini Evo enters **transfer mode** and
advertises differently. Connection in this state has two critical differences from a normal print session:

1. **GATT handle layout shifts**: The write characteristic moves from h=0x0014 to h=0x0014–0x0016 range;
   exact handles may differ between camera restarts. Use UUID-based lookup (via bleak) rather than
   hard-coded handles.

2. **Do NOT call `pair()`**: In transfer mode the camera accepts the BLE connection but immediately
   disconnects if `pair()` is called. The WinRT BLE stack uses cached bond keys automatically
   (the camera was previously paired in normal mode), so no explicit pairing call is needed.

3. **Wait 2 seconds after connect** before subscribing to the notify CCCD. Attempting to write CCCD
   too soon after connection causes "Attribute not found" errors as the camera is still setting up
   its GATT table.

4. **Camera may advertise with `name=None`** (no advertising name) in mid-state-transition.
   Scan by address once the name is confirmed, then connect.

```python
# Transfer-mode connect pattern (no pair() call):
dev = await BleakScanner.find_device_by_filter(
    lambda d, a: d.address.upper() == CAMERA_ADDR.upper(), timeout=30
)
client = BleakClient(dev, timeout=30)
await client.connect()
await asyncio.sleep(2.0)          # settle BEFORE subscribing
await client.start_notify(NOTIFY_UUID, handler)
```

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

**Status: Resolved — no missing BLE command.**

The camera automatically registers every ejected print in its internal history
(up to 50 entries, stored in camera flash) regardless of which BLE client triggered
the print. Confirmed 2026-05-17: prints sent via our tool are visible in the
on-camera print history without any additional commands.

The Instax app's "TRANSFERRED IMAGES" gallery is populated by a separate user-initiated
flow: the user selects "PRINTED IMAGE TRANSFER" from the camera's physical menu, which
causes the camera to push the stored JPEG back over BLE to the app. That read-back
transfer is not part of the print pipeline.

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

## Feature Roadmap

### 1. Live View (0x82 pull protocol) — CONFIRMED

**Status: Fully working on both Mini Evo (FI019) and Wide Evo (FI028). 2026-05-17.**

The `0x82` command group is **live view** — each `0x82,0x01` pull returns a **fresh, complete
JPEG of whatever the camera lens currently sees**. It is a real-time viewfinder stream used
for remote framing / remote shutter. It is **not** stored-print image transfer.

**Confirmed from btsnoop session 144 (Wide Evo):** 176 unique JPEG frames delivered in
8.57 s ≈ **20 fps**. Each frame is **160×106 px** (≈1 KB). Frames differ in content
(159 of 176 are unique), confirming a live feed and not a repeated still.

**Each `0x82,0x01` response payload format:**
```
[2B chunk_idx = 0x0001][3B frame header][JPEG bytes starting FFD8FF…]
```
The 3B frame header before SOI is not yet fully decoded. Strip 5 bytes (2B idx + 3B header)
to reach the raw JPEG. `chunk_idx` is always 1 — each pull is a self-contained single-chunk frame.

**Confirmed command flow:**

```
# ── Prepare + start ────────────────────────────────────────────────────────
Phone → cam:  op=(0x80,0x15)  payload=17×0x00             # LIVE_VIEW_PREPARE
cam → phone:  op=(0x80,0x15)  payload=17B                 # ACK (Mini Evo: [0xBF]; Wide Evo: 17B with byte[8]=0x32)
Phone → cam:  op=(0x82,0x00)  payload=[1B slot_index]     # LIVE_VIEW_START
cam → phone:  op=(0x82,0x00)  payload=[1B slot_index]     # ACK

# ── Pull loop (repeat for each frame, ~20 fps on Wide Evo) ────────────────
Phone → cam:  op=(0x82,0x01)  payload=<empty>             # pull request
cam → phone:  op=(0x82,0x01)  payload=[2B chunk_idx=0x0001][3B hdr][JPEG…]
  # Mini Evo: pull 1 = 1B readiness signal (0x02); pull 2 = full JPEG burst
  # Wide Evo: pull 1 = full JPEG burst (no readiness preamble)

# ── End ────────────────────────────────────────────────────────────────────
Phone → cam:  op=(0x82,0x02)  payload=[1B slot_index]     # LIVE_VIEW_END
cam → phone:  op=(0x82,0x02)  payload=[0x00]              # ACK
```

Note: `CAMERA_LOG_SLOT_ACK` (`0x84,0x0b`) is **not required** before `0x82` (confirmed
2026-06-17 — see §2). The 0x84 sequence runs independently before the 0x82 live-view loop.

**Observed JPEG sizes:**

| Camera | Resolution | JPEG size | Pulls needed | Source |
|---|---|---|---|---|
| Mini Evo (FI019) | ~120×160 px (portrait) | ~2.7 KB | 2 | Live capture |
| Wide Evo (FI028) | **160×106 px** (landscape) | **~1.0 KB** | 1–3 | btsnoop session 144 ✓ |

**Slot index semantics:** Unknown. Both cameras respond to indices 0 and 1. The slot
index does not select a stored image — it selects a viewfinder buffer slot.

---

### 2. Camera Print Log Download — CONFIRMED

**Status: Protocol fully decoded 2026-05-17 from HCI capture session 144.**

The `0x84` command group downloads the camera’s **date-indexed print log** — a record
of when prints were made and how many, by date. This is **not** visual thumbnail data.

**Context:** The app runs this sequence immediately after connecting (before 0x82). It
is triggered when the user presses the camera’s share button.

**Confirmed command flow:**

```
# ── Open a log session ──────────────────────────────────────────────────────────
Phone → cam:  op=(0x84,0x00)  payload=<empty>             # CAMERA_LOG_SUBTOTAL_START
cam → phone:  op=(0x84,0x00)  payload=[13B]              # summary (slot count etc.)

Phone → cam:  op=(0x84,0x01)  payload=[4B subtotal_data] # CAMERA_LOG_SUBTOTAL_DATA
cam → phone:  op=(0x84,0x01)  payload=[9B]              # cumulative totals

Phone → cam:  op=(0x84,0x02)  payload=<empty>             # CAMERA_LOG_SUBTOTAL_CLEAR
cam → phone:  op=(0x84,0x02)  payload=[1B 0x00]          # ACK  (marks log for deletion)

# ── Download one log slot ────────────────────────────────────────────────────
Phone → cam:  op=(0x84,0x09)  payload=[1B slot_id]        # CAMERA_LOG_SLOT_QUERY
cam → phone:  op=(0x84,0x09)  payload=[2B slot_id][4B data_size][4B data_size][4B record_count]
              # slot_id: 0 = print date log; 2 = filter/usage log. All-zeros if log cleared.
              # data_size: total bytes of log blob (e.g. 4908 for 3 records × 1636B each)

Phone → cam:  op=(0x84,0x0a)  payload=[1B slot_id][4×0x00]  # CAMERA_LOG_SLOT_DATA request
cam → phone:  op=(0x84,0x0a)  payload=[data_size B]      # camera immediately pushes log blob
              # delivered as fragmented IOS Link packet over multiple ATT notifications
              # blob format: [6B header][N × RECORD]
              # SLOT 0 (print count log) record layout (record_size=1636B):
              #   [6B zeros][8B date ASCII “YYYYMMDD”]
              #   [uint8 0x00][uint8 print_count][3B zeros][uint8 print_count_repeat]
              #   after_date[1] = after_date[5] = print count for that date
              # SLOT 2 (filter/usage log) record layout (record_size=1646B):
              #   [6B zeros][8B date ASCII “YYYYMMDD”]
              #   [2B zeros][uint8 field_3]...[uint8 field_7][uint8 field_9]...[uint8 field_15]
              #   all non-zero fields = 1 for a single print (per-field meaning TBD)
              # record_size matches data_size / record_count

Phone → cam:  op=(0x84,0x0b)  payload=[1B slot_id]        # CAMERA_LOG_SLOT_ACK
cam → phone:  op=(0x84,0x0b)  payload=[0x00][1B slot_id]  # ACK

# Repeat SLOT_QUERY / SLOT_DATA / SLOT_ACK for other slot_ids (e.g. 0 and 2)
```

**Observed values (Wide Evo, session 144, HCI capture 19-51-52, 2026-05-17 + live camera 2026-06-17):**
- Slot 0 (print date log): record_size=1636B
  - Fields: `after_date[1]` = `after_date[5]` = print count for that date
  - Session 144 (3 records): date=`20260324` count=1 / date=`20260124` count=19 / date=`20250416` count=1
  - Live camera (1 record):  date=`20260617` count=1 ✓
- Slot 2 (filter/usage log): record_size=1646B
  - `after_date` positions [3, 7, 9, 11, 13, 15] are all 1 for a single-print day (exact per-field meaning TBD)
  - Live camera (1 record):  date=`20260617`, all 6 positions = 1 (1 print, no filter data decoded yet)
- After `CAMERA_LOG_SUBTOTAL_CLEAR` + both ACKs: subsequent `0x84,0x09` queries return all-zeros
  (log is **destructively read** — cleared once the phone ACKs; camera on-screen history unaffected)

**Empty-slot quirk (camera firmware):** When a slot has no pending data,
`CAMERA_LOG_SLOT_DATA` delivers a 12-byte ATT notification even though `total_len`
claims 13 -- the trailing checksum byte is absent. Receive code should accept a
packet that is exactly 1 byte short after a 0.5 s grace wait (confirmed Wide Evo, 2026-06-17).

**`CAMERA_LOG_SLOT_ACK` not required before `0x82` (confirmed 2026-06-17):**
Omitting `0x84,0x0b` after downloading a slot does not prevent `0x80,0x15 PREPARE`
or `0x82,0x00 LIVE_VIEW_START` from succeeding. Skipping the ACK (and the prior
`CAMERA_LOG_SUBTOTAL_CLEAR`) preserves the log for re-reading on the next BLE session.

**Important:** The Instax app’s “History” display is this metadata (dates + counts),
not image thumbnails. Visual print images are NOT transmitted over BLE.

---

### 3. Remote Shutter

**Goal:** Trigger the camera's shutter remotely over BLE.

Now that 0x82 is confirmed as live view, the remote shutter is the next thing to
reverse-engineer. Candidates:
- `CAMERA_SETTINGS` (op1=0x80, op2=0x00) — write a "capture" setting
- `CAMERA_SETTINGS_GET` (0x80,0x01) — poll for shutter-ready state
- An undiscovered opcode (capture a BLE session during app remote-shutter use)

**What we need:** Capture an HCI log while using the Instax app's remote-shutter
feature and look for the trigger opcode.

---

## Hypotheses & Open Questions

This section tracks theories and ideas that are plausible but not yet confirmed.

### H1 — Why Mini Evo (Gen 1) does not respond to `(88,00)`

**Observed:** Connected to Mini Evo (FA:AB:BC:11:6F:D2), MTU=247, subscribed OK. Sent
`(88,00)` — camera sent nothing back in 20 s.

**Theory A (most likely):** The image_receive.py script never polled `CAMERA_FUNCTION_INFO`
first. The camera may require seeing the polling loop (simulating the real app's keepalive)
before it recognises the session as legitimate. Sending `(88,00)` cold, without the
normal session handshake, may be silently rejected.
→ *Test: add `(00,00)` hello + `(00,02)` InfoType=0x04 polling loop before `(88,00)`.*

**Theory B:** Gen 1 (FI019) and Gen 2 (FI028) have different opcodes for image transfer.
Gen 2 uses `0x88`; Gen 1 may use `0x86` or another family that was not in the
available btsnoop captures.
→ *Test: capture an HCI log of the Instax app doing an image transfer from the Mini Evo.*

**Theory C:** The transfer-ready flag is in a different InfoType on Gen 1. The flag
being in `CAMERA_FUNCTION_INFO` (InfoType=0x04) is confirmed only for Gen 2. Gen 1 may
put it in `PRINTER_FUNCTION_INFO` (InfoType=0x02) or another byte.
→ *Test: run listen_passive.py + poll all (00,02) InfoTypes while pressing Share; compare
payload snapshots.*

**Theory D:** Mini Evo firmware update (2026) changed the pairing/bonding flow and
may also have changed or added the 0x88 protocol. The btsnoop we have is from an older
firmware session — Gen 1 may have added 0x88 in the same update.

### H2 — Meaning of `CAMERA_FUNCTION_INFO` byte[0] and byte[1]

Normal Wide Evo value (new_capture): `03 50 00 00 00 00 00 00 00 05 04 01 00 00 00 00`
Keepalive value (19-51-52 capture, different state): `02 32 00 00 00 00 00 00 00 00 00 00 00 00 00 00`

- **byte[1] = 0x32 = 50**: appears in `LIVE_VIEW_PREPARE` response byte[8] on Wide Evo. Could
  be a capability register, print count, or mode identifier. Matches the decimal value 50
  (total prints taken per `CAMERA_HISTORY_INFO`? But that field is 0x17=23). Unknown.
- **byte[0] = 0x02 vs 0x03**: may encode a camera mode or state machine state
  (idle=0x02, live-view-active=0x03?).
- **byte[10] = 0x04, byte[11] = 0x01**: stable across samples. Likely a capability flags field.

### H3 — Correct polling loop before `(88,00)` (Gen 1)

The btsnoop shows the Wide Evo app sends a full session handshake before polling begins:
`(00,00)` hello → device info queries → `(00,02)` InfoType=0x04/05/02/03/01 rotation.
Our current scripts skip the handshake and go straight to `(88,00)`. Gen 1 may require
the handshake to initialise internal session state before 0x88 commands are accepted.
→ *Test: run the full handshake (`(00,00)` + device info + keepalive rotation) for ~5 s,
then press Share on camera, detect flag, fire `(88,00)`.*

### H4 — `(88,02)` chunk ACK vs data: two frames or one?

From the btsnoop we see two `(88,02)` cam→phone responses for each phone→cam request:
first a 4B ACK (`[00 00 00 00]`), then the large data frame. However, both have the same
op code. It is possible that:
- **Theory A**: these are always two separate IOS Link frames
- **Theory B**: the 4B "ACK" is actually the 5-byte header of a single large frame that
  arrives fragmented across two ATT notifications (4B + remaining)
The `recv_frame()` implementation in `image_receive.py` treats them as separate calls —
if they are actually one frame the code will misparse. Needs live capture to verify.

### H5 — Metadata byte[29] = 0x32

`0x88,0x01` metadata byte[29] = 0x32 = 50. Also appears in `CAMERA_FUNCTION_INFO` data[1]
and in `LIVE_VIEW_PREPARE` response byte[8] on Wide Evo. Possible meanings:
- Total digital transfers made by this camera (lifetime counter)
- A capability/mode register value that is coincidentally the same
- Camera print count (but `CAMERA_HISTORY_INFO` shows 0x17=23, not 50)

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
