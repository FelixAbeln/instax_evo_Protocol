# Link protocol

← [Wiki index](README.md)

The Instax Link protocol is the framing used by the Instax iOS app, by
[javl/InstaxBLE](https://github.com/javl/InstaxBLE), and by all current Evo
cameras (Mini Evo, Evo Wide, and the assumed Mini Evo Cinema). It runs over the
shared GATT service described in [overview.md](overview.md).

## Packet format

```
Request:   41 62  [length: uint16 BE]  [op1]  [op2]  [payload...]  [checksum]
Response:  61 42  [length: uint16 BE]  [op1]  [op2]  [payload...]  [checksum]
```

- `41 62` = `"Ab"` — phone to camera
- `61 42` = `"aB"` — camera to phone
- `length` = total packet size in bytes (including the 2-byte header and 1-byte checksum)
- `checksum` = `(255 - (sum(all_preceding_bytes) & 255)) & 255`
- Minimum packet (no payload) = 7 bytes: `41 62 00 07 [op1] [op2] [cs]`

> **Reading the `00` in `41 62 00 07 ...`:** the `00` is the *high byte* of the
> 2-byte big-endian length field (BLE packets are always < 256 bytes, so the
> high byte is always `0x00`). There is **no** 3-byte header — the format is
> `[41 62] [00 07]` = header(2) + length(2), not `[41 62 00] [07]`.

Checksum identity: `(sum(entire_packet) & 255) == 255`.

Packets > ~182 bytes are split into multiple BLE write commands; reassemble
before parsing.

## Python packet builder

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

## Opcode table

Sourced from [javl/InstaxBLE `Types.py`](https://github.com/javl/InstaxBLE/blob/main/Types.py),
cross-referenced with live Gen 2 Evo Wide HCI captures.

| op1 | op2 | Name | Notes |
|---|---|---|---|
| 0x00 | 0x00 | `SUPPORT_FUNCTION_AND_VERSION_INFO` | Init / hello — first packet sent |
| 0x00 | 0x01 | `DEVICE_INFO_SERVICE` | Device info (payload = InfoType byte) |
| 0x00 | 0x02 | `SUPPORT_FUNCTION_INFO` | Status / battery poll (payload = InfoType byte) |
| 0x00 | 0x10 | `IDENTIFY_INFORMATION` | |
| 0x01 | 0x00 | `SHUT_DOWN` | |
| 0x01 | 0x02 | `AUTO_SLEEP_SETTINGS` | |
| 0x10 | 0x00 | `PRINT_IMAGE_DOWNLOAD_START` | |
| 0x10 | 0x01 | `PRINT_IMAGE_DOWNLOAD_DATA` | Chunked image bytes |
| 0x10 | 0x02 | `PRINT_IMAGE_DOWNLOAD_END` | |
| 0x10 | 0x80 | `PRINT_IMAGE` | Trigger the print |
| 0x10 | 0x81 | `REJECT_FILM_COVER` | |
| 0x20 | 0x00 | `FW_DOWNLOAD_START` | Firmware update |
| 0x20 | 0x10 | `FW_PROGRAM_INFO` | Capability query; sent once at session start. P→C empty; C→P 3B `[00 00 00]`. |
| 0x30 | 0x00 | `XYZ_AXIS_INFO` | Accelerometer |
| 0x30 | 0x01 | `LED_PATTERN_SETTINGS` | |
| 0x80 | 0x00 | `CAMERA_SETTINGS` | Evo-specific camera setting write |
| 0x80 | 0x01 | `CAMERA_SETTINGS_GET` | Evo-specific camera setting read |
| 0x80 | 0x10 | *(Evo-specific session register)* | **Required for live HIST tracking.** P→C `[0x00]`; C→P 10B `[00 00 02 00 03 00 00 00 00 00]`. See [session-init.md](session-init.md). |
| 0x80 | 0x11 | *(register read/write)* | See [registers.md](registers.md). |
| 0x80 | 0x15 | `LIVE_VIEW_PREPARE` | Seen in queue/history image flows and some older live-view sessions. Do not treat it as a blanket prerequisite for current live-view control. See [live-view.md](live-view.md). |
| 0x82 | 0x00 | `LIVE_VIEW_START` | Open live-view session, slot index byte. |
| 0x82 | 0x01 | `LIVE_VIEW_FRAME` | Pull one JPEG frame. See [live-view.md](live-view.md). |
| 0x82 | 0x02 | `LIVE_VIEW_END` | Close live-view session. |
| 0x82 | 0x10 | `IMG_HIST_QUERY` | Begin the `0x82` picture receive flow. On FI028 this is the post-shutter transfer query. On FI019 it also works after the app-style live-view stop path, but not as a standalone command during active live view. See [auto-transfer.md](auto-transfer.md). |
| 0x82 | 0x20 | `IMG_HIST_POLL` | Poll until camera-encoded JPEG is ready. |
| 0x82 | 0x21 | `IMG_HIST_CHUNK` | Request / receive one chunk. |
| 0x82 | 0x22 | `IMG_HIST_END` | Close auto-transfer session. |
| 0x84 | 0x00 | `CAMERA_LOG_SUBTOTAL_START` / `HIST_INFO` | See [history-log.md](history-log.md). |
| 0x84 | 0x01 | `CAMERA_LOG_SUBTOTAL_DATA` / `HIST_INIT` | |
| 0x84 | 0x02 | `CAMERA_LOG_SUBTOTAL_CLEAR` / `HIST_START` | |
| 0x84 | 0x03 | `CAMERA_LOG_DATE_START` | |
| 0x84 | 0x06 | `CAMERA_LOG_FILTER_START` | |
| 0x84 | 0x09 | `CAMERA_LOG_SLOT_QUERY` / `HIST_LIST_REQ` | Slot metadata (size + record count). |
| 0x84 | 0x0a | `CAMERA_LOG_SLOT_DATA` / `HIST_GET_DATA` | Slot payload. |
| 0x84 | 0x0b | `CAMERA_LOG_SLOT_ACK` / `HIST_DONE` | Finalise slot read (clears buffer). |
| 0x85 | 0x00 | `DOWNLOAD_STATE_QUERY` | See [image-pull.md](image-pull.md). |
| 0x85 | 0x01 | `DOWNLOAD_INITIATE` | Triggers transfer-ready flag ~700 ms later. |
| 0x88 | 0x00 | `IMAGE_TRANSFER_START` | Phone-initiated share-button pull. See [image-pull.md](image-pull.md). |
| 0x88 | 0x01 | `IMAGE_TRANSFER_INFO` | 34-byte image metadata. |
| 0x88 | 0x02 | `IMAGE_TRANSFER_DATA` | One chunk per request. |
| 0x88 | 0x03 | `IMAGE_TRANSFER_END` | |
| 0x88 | 0x05 | `IMAGE_TRANSFER_RESULT` | |

## InfoType payloads

The two polling commands use **different** InfoType numbering spaces.

### `DEVICE_INFO_SERVICE` (op1=0x00, op2=0x01) — identity strings

Response payload format: `[0x00][InfoType_echo][str_len][str_bytes…]`

| Value | Name | Wide Evo (FI028) example |
|---|---|---|
| 0x00 | `MANUFACTURER` | `"FUJIFILM"` (8 chars) |
| 0x01 | `MODEL_ID` | `"FI028"` (5 chars) |
| 0x02 | `SERIAL` | `"92007814"` (8 chars — matches BLE name suffix) |
| 0x03 | `FW_MAIN` | `"0000"` |
| 0x04 | `FW_SUB` | `"0102"` |
| 0x05 | `FW_BLE` | `"0005"` |
| 0x09 / 0x0A | *(empty)* | `` |

### `SUPPORT_FUNCTION_INFO` (op1=0x00, op2=0x02) — camera status

Response payload format: `[0x00][InfoType_echo][data…]`

| Value | Name | Notes |
|---|---|---|
| 0x00 | `IMAGE_SUPPORT_INFO` | `[width: 2B BE][height: 2B BE][…]`. Always query first; use this for image prep. See [film dimensions](#film-dimensions-by-model--print-mode). |
| 0x01 | `BATTERY_INFO` | `[battery_state][battery_pct]`. State: 0=critical, 1=low, 2=medium, 3=high, 4=full. |
| 0x02 | `PRINTER_FUNCTION_INFO` | `[status_byte][0x00][shots_in_pack: 2B]…`. `photos_left = status_byte & 0x0F`, `charging = bool(status_byte & 0x80)`. |
| 0x03 | `PRINT_HISTORY_INFO` | `[uint32 BE: transfer_count][uint32 BE: print_count]` in current FI019/FI028 probes. These fields are readable and useful runtime counters, but they are **not** the same as [HIST `(84,xx)`](history-log.md) Usage History totals. |
| 0x04 | `CAMERA_FUNCTION_INFO` | 16B data. `data[2]` (= full payload[4]) = `0x01` when camera is **transfer-ready**; `0x00` at rest. Raised by camera ~700 ms after the phone sends `(0x85,0x01)`. |
| 0x05 | `CAMERA_HISTORY_INFO` | 6B response `[0x00][0x05][0x00][0x00][0x00][counter]`. **Counter at pay[5]** increments for every shot fired (including shots taken while BLE is connected to a third-party script). |

## Film dimensions by model / print mode

The camera reports its authoritative print dimensions via `IMAGE_SUPPORT_INFO`
(InfoType 0x00). **Always query this first** and use the response — never
hard-code dimensions by model name.

| Camera | Model ID | Film | `IMAGE_SUPPORT_INFO` (w×h) | Orientation | Chunk size | Source |
|---|---|---|---|---|---|---|
| Instax Mini Evo | FI019 | instax mini | **600 × 800** | Portrait | 900 B | Live capture ✓ |
| Instax Evo Wide | FI028 | instax Wide | **1260 × 840** | Landscape | 900 B | HCI log ✓ |
| Instax Mini Evo Cinema | unknown | instax mini | **800 × 600** | Landscape (cinema) | 900 B | Fujifilm spec |
| Instax Mini Link / Square Link | — | mini / Square | 600×800 / 800×800 | Portrait / Square | 900 / 1808 B | javl/InstaxBLE |

**Dimension convention:** `IMAGE_SUPPORT_INFO` always returns `(width, height)`
as two big-endian uint16. A portrait 600×800 image means 600 px wide × 800 px
tall; Cinema's 800×600 is 800 wide × 600 tall (landscape).

**Cinema note:** The Mini Evo Cinema uses the same physical instax mini film
cartridge as the Mini Evo, but prints in landscape orientation by rotating the
print head direction.

**Chunk size:** All Mini/Wide cameras use 900 bytes per
`PRINT_IMAGE_DOWNLOAD_DATA` chunk payload. The only known exception is Square
Link (1808 B), which uses a different film transport.

## Response byte layout

All `DEVICE_INFO_SERVICE` and `SUPPORT_FUNCTION_INFO` responses share the same
shape:

```
Byte:  0    1    2    3    4    5    6    7    8    9 …  last
       61   42  [len_hi len_lo]  op1  op2  00  InfoType  [data...]  cs
                                                ^^^^^^^^^
                                                2-byte prefix before actual data
```

Actual payload data always starts at **`response[8]`**.

## Parsing status responses

```python
# BATTERY_INFO (op=(0x00,0x02) InfoType=0x01)
battery_state, battery_pct = struct.unpack_from('>BB', response, 8)

# PRINTER_FUNCTION_INFO (op=(0x00,0x02) InfoType=0x02)
status_byte = response[8]
photos_left = status_byte & 0x0F     # low 4 bits
is_charging = bool(status_byte & 0x80)

# IMAGE_SUPPORT_INFO (op=(0x00,0x02) InfoType=0x00)
width, height = struct.unpack_from('>HH', response, 8)

# DEVICE_INFO_SERVICE strings (op=(0x00,0x01))
str_len = response[8]
text = response[9:9 + str_len].decode('ascii')
```
