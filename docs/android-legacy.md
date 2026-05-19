# Android profile (legacy — not used)

← [Wiki index](README.md)

Every Instax camera with BLE advertises **two separate BLE profiles**
simultaneously. The Android profile is used only by the Instax Android app and
speaks a completely different on-wire protocol from the Link protocol. **This
project does not use the Android profile.**

This page is kept only as a historical reference. All current work targets the
[Link protocol](link-protocol.md) on the `FA:AB:BC:…` BLE address.

## Profile identification

| | Link profile | Android profile |
|---|---|---|
| BLE address (Mini Evo) | `FA:AB:BC:11:6F:D2` | `E0:48:24:D7:CF:2E` |
| Adv name suffix | `(IOS)` or `(BLE)` | (varies) |
| Wire format | `41 62` / `61 42` framed packets | Bare `16xx` / `17xx` writes |
| Used by | iOS app, javl/InstaxBLE, **this project** | Instax Android app only |
| GATT service UUID | `70954782-…` (same) | `70954782-…` (same) |
| GATT handles | `0x0014` write / `0x0016` notify | `0x002A` write / `0x0027` notify |

Despite sharing the same GATT service UUID, the two profiles use **different
handles** and **completely different application protocols**.

## Brief protocol summary (Gen 1 Mini Evo Android profile)

Captured in the 17-34-32 HCI log; left intact only for reference.

### Device-specific DEVICE_ID (8 bytes, gen 1 Mini Evo)

```
8d 3d b0 e5 92 59 03 3d
```

### Handshake writes

```
Write h=0x002A: 00 05  [DEVICE_ID 8b]  00 00         (12 bytes, WriteCommand)
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
- `16 01 00 03 44` — battery_level = byte[3] = 0x03 (scale 0–4, "3 pips" full)
- `16 02 01 02 02` — film count field = byte[2] = 0x01 → 1 shot remaining

### Keep-alive pings

Every ~25 seconds: `19 00 [seq]` / `1B 00 [seq]` on h=0x0027 / h=0x001D.

## Full historical capture notes

The following notes are extracted from the 17-34-32 Android HCI capture
(2026-05-16, Mini Evo `E0:48:24:D7:CF:2E`). They document protocol behaviour
seen on the wire; the project does not implement any of it.

### Android profile GATT handles

| Handle | Service | Purpose |
|---|---|---|
| h=0x0020 | `0x1849` custom | Generic write channel (407 events) |
| h=0x001D | `0x1849` custom | Notifications from h=0x0020 channel (365 events) |
| h=0x002A | `0x1849` custom | DEVICE_ID-auth write channel (56 events) |
| h=0x0027 | `0x1849` custom | Notifications from h=0x002A channel (126 events) |
| h=0x0028 | `0x1849` custom | Low-activity custom handle |
| h=0x000D | `0x1800` GAP | Setup writes early session |
| h=0x0013 | `0x180A` DIS | Device Info characteristic |
| h=0x0016 | `0x180A` DIS | Device Info read source |
| h=0x001E | `0x1800` GAP | GAP write target |
| h=0x0003 | `0x1800` GAP | GAP indication target (rare) |

Services discovered: `0x1800` GAP, `0x1801` GATT, `0x180A` DIS, `0x1849`
(custom Fujifilm primary), `0x184C` (custom Fujifilm secondary),
`5511be18-3c47-5478-1fc2-4b4b5dcdaf62` (custom 128-bit vendor UUID).

Notifications on h=0x001D and h=0x0027 are **mirrored** — the same data
arrives on both channels.

### Notify packet types

Decoded from live HCI analysis (session 17-34-32):

**Type A — 5-byte status message** `[type] [subtype] [value] [b3] [b4]`

| Subtype | Example | Meaning |
|---|---|---|
| `01` | `16 01 00 03 44` | Init/battery; `b3` = battery level (0–3 pips). `03` = full |
| `02` | `16 02 01 02 02` | Image count; `b2` = images queued. `01` = 1 image loaded |

- `type` byte: `0x16` on h=0x0027, `0x17` on h=0x001D (mirror).
- Battery field is **byte [3]** of subtype-01 packets.
- Image count field is **byte [2]** of subtype-02 packets.

**Type B — 3-byte keep-alive ping** `[msg_id] [00] [seq]`

```
19 00 2E   # h=0x0027 ping, seq increments per ping (~25 s interval)
1B 00 2E   # h=0x001D ping (slightly offset seq)
```

Seq is a **global counter across reconnections** — a follow-up session
(17-52-45) resumed from seq `0x56` rather than resetting to zero.

**Type C — 6-byte device ID/firmware** `[type] [sub] [4 bytes]`

Example: `16 00 D6 B7 7B 1B` — appears once at session start; likely firmware
version or device ID bytes.

**Type D — 13-byte session init** `[header] [6B device_id] [padding] [state]`

Example: `00 06 8D 3D B0 E5 92 59 03 3D 00 00 01`.
- Bytes [2–7]: 6-byte device identifier (changes per device).
- Last byte `01`: initial state flag.

### Status notifications starting with `0xA8`

```
A8 [seq] 00 [type] [data…]
```

- `0xA8` = message class (status notification).
- `[seq]` = increments per message.
- `[type]`: `0x02` = status (during setup/transfer), `0x52` = completion/
  phase transition (followed by variable-length payload, observed up to 100+ B).

Status type `0x02` payloads contain an `11 01 04 [device_id]` pattern at
offset +4 and `02 XX` at offset +10 (status codes 3–5 observed).

### Confirmed semantics

Both verified by physical Mini Evo behaviour during session 17-34-32 (battery
pips on the camera matched the BLE value; ejected film matched the queued
image count):

- Battery level = byte[3] of 5-byte subtype-01 packet (`16 01 00 03 44` → 3 pips).
- Image count = byte[2] of 5-byte subtype-02 packet (`16 02 01 02 02` → 1 queued).

### Session capture stats

Main ATT log (`...btsnoop_hci.log.last`) for session 17-34-32: 1 253 ATT
packets (641 device→host, 612 host→device). Dominant ATT opcodes: Handle
Value Notification (491), Write Command (462). Bursty session pattern with
~25 s idle gaps between activity windows; first activity burst 2.66 s–36.08 s
covered 345 ATT packets.

### Relationship to the Link protocol

Despite using a completely different on-wire format, the Android profile
appears to be a transport-layer reframing of the same underlying state
machine:

- Both expose the same `70954782-…` primary service UUID for discovery.
- Both encode battery level, photo count, and a session-init device ID.
- The Link protocol's `(00,02)` InfoType=1 (battery) and InfoType=2
  (photos_left) map directly to subtype-01 and subtype-02 above.

The `0xA8` status format and Type C/D device-ID handshake have no direct
Link-protocol equivalent and may be specific to the Android stack.
