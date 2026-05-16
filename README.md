# instax-evo-lab

Reverse-engineering the **Fujifilm Instax Mini Evo BLE print protocol**.

This project documents the full BLE protocol used by the Instax Evo camera family
and provides a working Python implementation that can print images directly from a PC
over Bluetooth — no official app required.

**Status:** Physical print confirmed on Gen 1 Mini Evo (FI019). Film ejected, image visible.

---

## Key protocol discoveries

### Two BLE profiles, two completely different protocols

Every Instax Evo camera advertises **two simultaneous BLE profiles** with different MAC address prefixes:

| Profile | Address prefix | Protocol | Used by |
|---|---|---|---|
| **IOS** | `FA:AB:BC:xx:xx:xx` | Link protocol — `41 62` framed packets | iOS app, javl/InstaxBLE, **this project** |
| **Android** | `E0:48:24:xx:xx:xx` | Legacy binary — `16 xx` / `17 xx` writes | Android app only |

Both profiles expose **the same GATT service and characteristic UUIDs**, which makes
the Android profile look deceptively similar until you look at the packet content.
The IOS profile is the correct target for everything in this project.

### Packet framing (IOS / Link protocol)

```
Request  (phone → camera):  41 62  [len: uint16 BE]  [op1]  [op2]  [payload...]  [checksum]
Response (camera → phone):  61 42  [len: uint16 BE]  [op1]  [op2]  [payload...]  [checksum]
```

- `41 62` = ASCII `"Ab"` — `61 42` = ASCII `"aB"` (case-flipped on response)
- `len` = total packet bytes including the 2-byte header and 1-byte checksum
- `checksum` = `(255 - sum(all_preceding_bytes)) & 255`
- Minimum packet (no payload): 7 bytes — `41 62 00 07 [op1] [op2] [cs]`
- Packets > ~182 bytes are split across multiple BLE write-without-response calls

```python
def create_packet(op1: int, op2: int, payload: bytes = b'') -> bytes:
    header = b'\x41\x62'
    length = struct.pack('>H', 7 + len(payload))
    body   = header + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])
```

### Full print sequence (confirmed working)

```
op=(0x00,0x00)  []                              SUPPORT_FUNCTION_AND_VERSION_INFO (hello)
op=(0x00,0x01)  [InfoType]                      DEVICE_INFO_SERVICE (model, serial, film size)
op=(0x00,0x02)  [0x01]                          BATTERY_INFO
op=(0x00,0x02)  [0x02]                          PRINTER_FUNCTION_INFO → photos_left

op=(0x10,0x00)  [img_size: 4B BE]               PRINT_IMAGE_DOWNLOAD_START
× N chunks:
op=(0x10,0x01)  [seq: 4B BE] [900 bytes]        PRINT_IMAGE_DOWNLOAD_DATA  ← ACK each chunk
op=(0x10,0x02)  []                              PRINT_IMAGE_DOWNLOAD_END

op=(0x10,0x80)  []                              PRINT_IMAGE  ← film ejects here
    ↳ response payload: [0x00, 0x0C]  (0x0C = print initiated)

op=(0x00,0x02)  [0x02]                          PRINTER_FUNCTION_INFO  (post-print, photos_left--)
```

- Each chunk is **900 bytes of image data** prefixed by a **4-byte big-endian sequence number** (total 904-byte payload)
- The last chunk is zero-padded to 900 bytes
- Every `PRINT_IMAGE_DOWNLOAD_DATA` must be ACKed by the camera before the next is sent
- `PRINT_IMAGE` (0x10, 0x80) is the only command that causes physical film ejection

### Image format the camera expects

| Property | Value |
|---|---|
| Dimensions | **600 × 800 px** (reported by `IMAGE_SUPPORT_INFO`, varies by film type) |
| Format | JPEG |
| Size | **≤ 105 KB** (camera buffer limit, empirically determined) |
| Colour | RGB |

### Film dimensions by model

| Model | Film | Dimensions | Chunk size |
|---|---|---|---|
| Mini Evo (FI019) | Instax Mini | 600 × 800 px | 900 B |
| Evo Wide (FI028) | Instax Wide | 1260 × 840 px | 900 B |
| Mini Link / Square Link | Mini / Square | 600×800 / 800×800 | 900 / 1808 B |

### Key opcodes

| op1 | op2 | Name | Notes |
|---|---|---|---|
| 0x00 | 0x00 | `SUPPORT_FUNCTION_AND_VERSION_INFO` | Hello — first packet every session |
| 0x00 | 0x01 | `DEVICE_INFO_SERVICE` | Model, serial, film size (InfoType byte payload) |
| 0x00 | 0x02 | `SUPPORT_FUNCTION_INFO` | Battery + film count (InfoType byte payload) |
| 0x10 | 0x00 | `PRINT_IMAGE_DOWNLOAD_START` | Payload = image byte count as uint32 BE |
| 0x10 | 0x01 | `PRINT_IMAGE_DOWNLOAD_DATA` | Payload = seq (4B BE) + 900B chunk |
| 0x10 | 0x02 | `PRINT_IMAGE_DOWNLOAD_END` | No payload |
| 0x10 | 0x80 | `PRINT_IMAGE` | No payload — triggers physical film ejection |
| 0x20 | 0x10 | `FW_PROGRAM_INFO` | Firmware version |
| 0x80 | 0x00 | `CAMERA_SETTINGS` | Evo-specific camera config write |
| 0x80 | 0x01 | `CAMERA_SETTINGS_GET` | Evo-specific camera config read |
| 0x84 | 0x00 | `CAMERA_LOG_SUBTOTAL_START` | Lifetime shot count (NOT remaining shots) |

### InfoType values (payload byte for 0x00,0x01 and 0x00,0x02)

| Value | Name | Response content |
|---|---|---|
| 0x00 | `IMAGE_SUPPORT_INFO` | Two uint16 BE = (width, height) |
| 0x01 | `BATTERY_INFO` | `[state][pct]` — state 0=critical…4=full, pct 0–100 |
| 0x02 | `PRINTER_FUNCTION_INFO` | `status_byte`: low 4 bits = photos left, bit 7 = charging |
| 0x03 | `PRINT_HISTORY_INFO` | (not yet decoded) |
| 0x05 | `CAMERA_HISTORY_INFO` | (not yet decoded) |

### GATT UUIDs (shared across all models and both profiles)

| UUID | Role |
|---|---|
| `70954782-2d83-473d-9e5f-81e1d02d5273` | Primary service |
| `70954783-2d83-473d-9e5f-81e1d02d5273` | **Write characteristic** |
| `70954784-2d83-473d-9e5f-81e1d02d5273` | **Notify characteristic** |

### Image modes (client-side only)

The camera has no on-device filter processing. All effects are applied in Python
before the JPEG is sent. The camera always receives raw pixel data.

| Mode | Implementation | Effect |
|---|---|---|
| Normal | — | No change |
| Rich | `ImageEnhance.Color(img).enhance(1.5)` | Saturation ×1.5 — vivid colours |

### Known gap — print history not updated

After a successful print the image does not appear in the Instax app's
"previously printed images" gallery. The official app likely sends an additional
BLE command (possibly in the `0x84` log family or `0x80` settings family) to register
the print. The opcode has not yet been captured. A new official-app HCI capture
is needed to identify it.

---

## Relation to javl/InstaxBLE

The [javl/InstaxBLE](https://github.com/javl/InstaxBLE) library documents and implements
the same Link protocol for **Instax Link printers** (dedicated printer hardware, no camera).
This project extends and adapts that work for the **Instax Evo** camera line.

### What was reused

- GATT service/characteristic UUIDs
- Packet framing (`41 62` header, uint16 BE length, XOR checksum)
- Opcode table (`EventType`) and `InfoType` values
- 900-byte chunk size for Mini film

### What is different or new

| Aspect | javl/InstaxBLE (Link printers) | This project (Evo cameras) |
|---|---|---|
| **BLE profiles** | Single IOS profile | Two simultaneous profiles; must target IOS specifically |
| **Pairing** | Not required | Required — passkey on camera display; call `client.pair()` each session |
| **Image preparation** | Caller provides image | Auto-resize to film dimensions, binary-search JPEG quality to ≤ 105 KB |
| **Filters** | Not in scope | Rich mode via PIL (client-side, no camera command needed) |
| **Print safety** | Immediate print | `--enable-print` flag required; default is data-only (no ejection) |
| **Post-print poll** | Not polled | `PRINTER_FUNCTION_INFO` re-polled; `photos_left` updated |
| **Android profile** | Not applicable | Fully separate legacy binary protocol (`16 xx` writes) — documented but not used |
| **Platform** | macOS / Linux primary | Windows 11 + bleak, exclusively |

---

## Setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**First-time camera pairing:** On the Instax Mini Evo, open Bluetooth settings → "Pair new device". Pair with `INSTAX-xxxxxxx (IOS)` in Windows Bluetooth settings. Enter the passkey shown on the camera. Only needed once — the tool re-establishes the encrypted session automatically on subsequent runs.

---

## Usage

```powershell
# Transfer image data only (no ejection — safe for testing):
python -m instax_lab evo-print image.jpg

# Transfer and print (ejects film):
python -m instax_lab evo-print image.jpg --enable-print

# Verbose — shows every BLE packet sent and received:
python -m instax_lab evo-print image.jpg --enable-print --verbose

# Other tools:
python -m instax_lab scan
python -m instax_lab inspect "FA:AB:BC:11:6F:D2"
python -m instax_lab notify  "FA:AB:BC:11:6F:D2"
python -m instax_lab replay  "FA:AB:BC:11:6F:D2" captures\sample-writes.jsonl
python -m instax_lab extract-captures captures
python -m instax_lab.capture   # pull Android HCI snoop log via adb
```

---

## Full protocol reference

See [docs/protocol.md](docs/protocol.md) for:
- Complete opcode and InfoType tables
- Full GATT handle maps for Gen 1 and Gen 2
- Legacy Android protocol decode
- Session transcripts from HCI captures
- Known gaps and next investigation steps


Sends images directly to the camera over Bluetooth — no official app required.
A physical print (film ejection + visible image) has been confirmed working.

---

## What this does

- **Prints images** over BLE to Instax Mini Evo (Gen 1 / FI019), with Rich-mode saturation filter
- Scans for nearby BLE devices and inspects GATT services
- Subscribes to notifications and logs them to JSONL
- Replays JSONL write captures for protocol replay testing
- Pulls Android HCI snoop logs via `adb bugreport` for further analysis
- Keeps a local print log (`captures/print-log.jsonl`) of every transferred/printed image

See [docs/protocol.md](docs/protocol.md) for the full reverse-engineered protocol specification.

---

## Credits and prior work

This project is based on the BLE protocol documented and implemented in
**[javl/InstaxBLE](https://github.com/javl/InstaxBLE)** (MIT licence).
The packet framing, opcode table, and GATT UUIDs were taken from that project
and cross-referenced against our own HCI captures.

### What we reused from javl/InstaxBLE

| Component | Source |
|---|---|
| GATT service/characteristic UUIDs | Taken directly |
| Packet format (`41 62` header, uint16 BE length, XOR checksum) | Taken directly |
| Opcode table (`EventType`) — `PRINT_IMAGE_DOWNLOAD_*`, `PRINT_IMAGE`, etc. | Taken directly |
| `InfoType` values for battery, film count, image size queries | Taken directly |
| 900-byte chunk size for Mini film | Taken directly |

### What is different / extended

| Aspect | javl/InstaxBLE (Mini/Square/Wide Link) | This project (Instax Mini Evo) |
|---|---|---|
| **Target device** | Instax Link printers (dedicated printer hardware) | Instax Mini Evo (hybrid camera + printer) |
| **BLE profiles** | Single IOS profile | Two simultaneous profiles: IOS (Link protocol) + Android (legacy binary) |
| **Pairing** | Not required | **Required** — camera shows a passkey; must pair once via OS Bluetooth settings, then call `client.pair()` each session |
| **Connection** | Direct connect to any Instax IOS device | Must target `INSTAX-xxxxx (IOS)` specifically; Android address (`E0:48:24:...`) speaks a different protocol |
| **Image preparation** | Caller provides pre-sized image | Built-in: auto-resize to 600×800 with LANCZOS, JPEG quality binary-search to hit 94.5–105 KB |
| **Filters / modes** | Not in scope | **Rich mode**: PIL `ImageEnhance.Color(img).enhance(1.5)` — client-side saturation ×1.5 before send |
| **Print safety** | Print triggers immediately | `--enable-print` flag required to eject film; default is data-only safe mode |
| **Post-print status** | Not polled | `PRINTER_FUNCTION_INFO` re-polled after print; `photos_left` updated |
| **Print log** | None | Appends to `captures/print-log.jsonl` on every run |
| **Windows support** | Primary target is macOS/Linux | Tested exclusively on Windows 11 with bleak |
| **Protocol research** | Complete for Link printers | IOS profile fully working; print-history registration command still under investigation |

---

## Camera compatibility

| Model | ID | Gen | Film | Status |
|---|---|---|---|---|
| Instax Mini Evo | FI019 | 1 | Mini 600×800 | ✅ Fully working — print confirmed |
| Instax Evo Wide | FI028 | 2 | Wide 1260×840 | 🔬 Protocol decoded from IOS HCI capture; untested live |
| Instax Mini Evo Cinema | unknown | 3 | Mini | ❓ Not captured; assumed same Link protocol |
| Instax Mini/Square/Wide Link | various | — | Mini/Square/Wide | Handled by javl/InstaxBLE; same protocol, no pairing needed |

---

## Requirements

- Windows 10 / 11
- Python 3.11+
- Bluetooth adapter (built-in or USB)
- Instax Mini Evo (or compatible camera)
- Android phone (for HCI snoop capture of the official app — optional)

---

## Setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks script activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

### First-time camera pairing

1. On the Instax Mini Evo, open Bluetooth settings and select "Pair new device".
2. In Windows Bluetooth settings, pair with `INSTAX-xxxxxxx (IOS)`. Enter the passkey shown on camera.
3. You only need to do this once. The tool calls `pair()` automatically on every subsequent connection to re-establish the encrypted session.

---

## Commands

### Print an image

Send image data only (safe — no film ejection):

```powershell
python -m instax_lab evo-print path\to\image.jpg
```

Send and physically print (ejects film):

```powershell
python -m instax_lab evo-print path\to\image.jpg --enable-print
```

Verbose output (shows every BLE packet):

```powershell
python -m instax_lab evo-print path\to\image.jpg --enable-print --verbose
```

Target a specific camera address (skip auto-scan):

```powershell
python -m instax_lab evo-print path\to\image.jpg --address "FA:AB:BC:11:6F:D2" --enable-print
```

The tool automatically:
- Resizes the image to 600×800 px (or the camera's reported film size)
- Binary-searches JPEG quality to reach 94.5–105 KB
- Sends all chunks and waits for ACK on each one
- Optionally triggers the print (`--enable-print`)
- Logs the result to `captures/print-log.jsonl`

### Scan for BLE devices

```powershell
python -m instax_lab scan --timeout 10
```

### Inspect a device's GATT table

```powershell
python -m instax_lab inspect "FA:AB:BC:11:6F:D2"
```

### Subscribe to notifications

```powershell
python -m instax_lab notify "FA:AB:BC:11:6F:D2"
```

### Replay a JSONL write capture

```powershell
python -m instax_lab replay "FA:AB:BC:11:6F:D2" captures\sample-writes.jsonl
```

### Pull Android HCI snoop logs (for protocol analysis)

```powershell
python -m instax_lab.capture
python -m instax_lab.capture --help
```

### Extract btsnoop logs from Android bugreport zips

```powershell
python -m instax_lab extract-captures captures
python -m instax_lab extract-captures captures --keep-source
python -m instax_lab extract-captures captures --dry-run
```

---

## Image modes

All filters are applied client-side (in Python/PIL) before transmission. The camera has no knowledge of which mode was used — it always receives raw JPEG pixels.

| Mode | How to apply | Effect |
|---|---|---|
| Normal | Default | No change to source image |
| Rich | `ImageEnhance.Color(img).enhance(1.5)` | Saturation ×1.5 — vivid, punchy colours |

To apply Rich mode manually before printing, use `instax_lab.evo_protocol.apply_rich_filter(img)`,
or pre-process the image in Pillow and pass the result to `print_image()`.

---

## Print log

Every `evo-print` run appends a line to `captures/print-log.jsonl`:

```json
{
  "t": 1747397000.0,
  "image": "F:\\path\\to\\image.jpg",
  "camera": "FA:AB:BC:11:6F:D2",
  "model": "FI019",
  "transferred": true,
  "printed": true,
  "photos_left_after": 0
}
```

`printed: false` means `--enable-print` was not passed (image data sent, no ejection).

---

## Known limitations

- **Print history not updated** — after printing, the image does not appear in the Instax app's "previously printed images" gallery. There is likely a missing BLE command to register the print in the camera's history log. Requires a new official-app capture to identify. See [docs/protocol.md](docs/protocol.md#known-gaps--commands-not-yet-identified).
- **Windows BLE addresses** — on Windows, BLE addresses may appear as UUID-like device IDs rather than `AA:BB:CC:DD:EE:FF` format. Use the exact string printed by `scan`.
- **Gen 2 / Evo Wide untested live** — protocol is decoded from HCI captures but no live print has been attempted.

---

## Protocol documentation

Full reverse-engineering notes, packet formats, opcode tables, and session transcripts are in [docs/protocol.md](docs/protocol.md).

