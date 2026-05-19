# Session initialisation

← [Wiki index](README.md)

This is the complete sequence sent at the start of every Link-protocol session,
before any print, live view, image pull, or HIST read.

## Full handshake flow

```
┌─ Connect ─────────────────────────────────────────────────────────────────────┐
│ Subscribe to notify char 70954784-2d83-473d-9e5f-81e1d02d5273                │
│ Write CCCD = 01 00  (enable notifications)                                   │
│ client.pair()  ← re-establish encrypted session (Gen 1 requires this)        │
└───────────────────────────────────────────────────────────────────────────────┘

SEND  op=(0x00,0x00)  payload=[]
  → SUPPORT_FUNCTION_AND_VERSION_INFO  ("hello" / session init)
RECV  op=(0x00,0x00)  payload=[version bytes]

SEND  op=(0x00,0x01)  payload=[0x00]
  → DEVICE_INFO_SERVICE  InfoType=MANUFACTURER  ("FUJIFILM")
RECV  op=(0x00,0x01)  payload=[0x00, 0x00, str_len, str_bytes...]

SEND  op=(0x00,0x01)  payload=[0x01]
  → DEVICE_INFO_SERVICE  InfoType=MODEL_ID  ("FI019", "FI028", ...)

SEND  op=(0x00,0x01)  payload=[0x02]
  → DEVICE_INFO_SERVICE  InfoType=SERIAL  (matches BLE name suffix)

SEND  op=(0x00,0x02)  payload=[0x00]
  → SUPPORT_FUNCTION_INFO  InfoType=IMAGE_SUPPORT_INFO   → film dims
SEND  op=(0x00,0x02)  payload=[0x01]
  → SUPPORT_FUNCTION_INFO  InfoType=BATTERY_INFO
SEND  op=(0x00,0x02)  payload=[0x02]
  → SUPPORT_FUNCTION_INFO  InfoType=PRINTER_FUNCTION_INFO

# --- The two Evo-specific session registers (required for HIST tracking) ---
SEND  op=(0x20,0x10)  payload=[]
  → FW_PROGRAM_INFO  (camera replies 3B [00 00 00])

SEND  op=(0x80,0x10)  payload=[0x00]
  → Evo session register
  → RECV 10B [00 00 02 00 03 00 00 00 00 00]
  → WITHOUT THIS, THE CAMERA DOES NOT LOG SHOTS TO HIST WHILE BLE IS CONNECTED.
```

## Minimal Python sequence

```python
# Connect via bleak; subscribe to notify char 70954784-...
# Gen 1 also requires client.pair()  before subscribing.

await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x00))               # hello
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x01, b'\x00'))      # mfr
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x01, b'\x01'))      # model
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x01, b'\x02'))      # serial
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x02, b'\x00'))      # image size
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x02, b'\x01'))      # battery
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x02, b'\x02'))      # photos left

# Required to enable live HIST tracking (Evo-specific)
await client.write_gatt_char(WRITE_CHAR, create_packet(0x20, 0x10))               # FW_PROGRAM_INFO
await client.write_gatt_char(WRITE_CHAR, create_packet(0x80, 0x10, b'\x00'))      # session reg
```

## Why `(0x80,0x10)` matters

The `(0x80,0x10)` register tells the camera "a Link-protocol app is connected
and wants to track usage". If it is **not** sent, the camera stops writing shot
records to its internal HIST buffer for the duration of the BLE session — so
any shots taken while connected will be invisible to `(0x84,xx)` HIST reads.

Confirmed btsnoop 2026-05-18 (Gen 2 Evo Wide): the official Instax app sends
this packet exactly once per session, immediately after `(0x20,0x10)`.

## Full Gen 2 observed handshake (Evo Wide FI028)

From HCI capture 19-51-52. All four captured BLE connections are identical —
no session state changes. Confirmed by live testing 2026-05-17.

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
op=(0x00,0x02)  [0x00]                IMAGE_SUPPORT_INFO → 1260×840 (Wide)
op=(0x20,0x10)  []                    FW_PROGRAM_INFO — C→P 3B `[00 00 00]`
op=(0x80,0x10)  [0x00]                Evo session register — enables live HIST
                                      C→P 10B `[00 00 02 00 03 00 00 00 00 00]`
op=(0x80,0x11)  [0x0B, 0,0,0,0]       reg 0x0B → 2   (Flash/WB)
op=(0x80,0x11)  [0x0C, 0,0,0,0]       → 0   (suspected Film Style)
op=(0x80,0x11)  [0x13–0x15, ...]      → 0   (unknown)
op=(0x80,0x11)  [0x16, 0,0,0,0]       value=0, param=0x32=50 (Exposure comp ±50)
op=(0x80,0x11)  [0x17, 0,0,0,0]       → 1   (Film Effect 1=Normal)
op=(0x80,0x11)  [0x18..0x1A, ...]     → 0   (unknown)
op=(0x80,0x11)  [0x1B, 0,0,0,0]       → 1   (Lens Effect 1=Normal)
op=(0x84,0x00)  []                    HIST_INIT → 13B [00000000 04000000 04000000 00]
op=(0x84,0x01)  [00 00 00 00]         HIST_SCHED → 9B (all zeros)
op=(0x84,0x02)  []                    HIST_COMMIT → [00]
op=(0x84,0x09)  [0x00]                HIST_LIST_REQ slot=00 → 14B
op=(0x84,0x09)  [0x02]                HIST_LIST_REQ slot=02 → 14B
op=(0x84,0x0b)  [0x00]                HIST_SLOT slot=0 → [00 00]
op=(0x84,0x0b)  [0x02]                HIST_SLOT slot=2 → [00 02]
# [if slot 2 count > 0: 0x80,0x15 + 0x82,0x00 + N× 0x82,0x01 + 0x82,0x02]
[app then polls SUPPORT_FUNCTION_INFO InfoTypes 04,05,02,03,01 in rotation every ~0.5s]
```

The `(0x80,0x11)` register sweep is **optional** — the app reads them to mirror
current camera settings in its UI. Skipping the sweep does not affect any other
functionality. See [registers.md](registers.md) for full register semantics.

## Connection notes

- **Gen 1 (Mini Evo)** requires passkey/PIN pairing after the OOBE firmware
  update. The pairing must be re-established on every fresh BLE connection.
  `bleak`'s `client.pair()` handles this on Windows.
- **Gen 2 (Evo Wide)** does **not** require pairing for the Link profile — Just
  Works connection succeeds without any UI prompt.
- The Wide Evo's BLE name ends in `(BLE)` instead of `(IOS)`. Filter on
  service UUID `70954782-…`, not on the advertising name suffix.
