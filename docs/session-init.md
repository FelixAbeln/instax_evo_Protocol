# Session initialisation

← [Wiki index](README.md)

This is the complete sequence sent at the start of every Link-protocol session,
before any print, live view, image pull, or HIST read.

## Gen 1 vs Gen 2 at init time

| Step | Gen 1 FI019 (Mini Evo) | Gen 2 FI028 (Evo Wide) |
|---|---|---|
| Explicit `client.pair()` | Not part of the maintained app flow; OS-level pairing may still be needed once on Windows | Not part of the maintained app flow |
| `(0x00,0x00)` + `(0x00,0x01/0x02)` status hello | Required | Required |
| `(0x20,0x10)` + `(0x80,0x10,[00])` | Not required for baseline status/print probes | Required for live HIST tracking while connected |
| `(0x80,0x11)` register sweep | Optional | Optional |
| `(0x84,xx)` HIST startup | Optional / not fully mapped on FI019 | Used heavily; fully mapped |

Practical rule: always run the common `(0x00,xx)` handshake first. Add
`(0x20,0x10)` + `(0x80,0x10)` when you need Gen 2 live HIST behavior.

## Full handshake flow

```
┌─ Connect ─────────────────────────────────────────────────────────────────────┐
│ Subscribe to notify char 70954784-2d83-473d-9e5f-81e1d02d5273                │
│ Write CCCD = 01 00  (enable notifications)                                   │
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

# --- The two Gen 2 Evo-specific session registers (HIST tracking) ---
SEND  op=(0x20,0x10)  payload=[]
  → FW_PROGRAM_INFO  (camera replies 3B [00 00 00])

SEND  op=(0x80,0x10)  payload=[0x00]
  → Evo session register
  → RECV 10B [00 00 02 00 03 00 00 00 00 00]
  → On FI028: without this, the camera does not log shots to HIST while BLE is connected.
  → On FI019: this sequence is not required for baseline status/counter reads.
```

## Minimal Python sequence

```python
# Connect via bleak; subscribe to notify char 70954784-...

await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x00))               # hello
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x01, b'\x00'))      # mfr
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x01, b'\x01'))      # model
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x01, b'\x02'))      # serial
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x02, b'\x00'))      # image size
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x02, b'\x01'))      # battery
await client.write_gatt_char(WRITE_CHAR, create_packet(0x00, 0x02, b'\x02'))      # photos left

# Required to enable live HIST tracking on Gen 2 (FI028)
await client.write_gatt_char(WRITE_CHAR, create_packet(0x20, 0x10))               # FW_PROGRAM_INFO
await client.write_gatt_char(WRITE_CHAR, create_packet(0x80, 0x10, b'\x00'))      # session reg
```

## Full Gen 1 baseline init observed (Mini Evo FI019)

Probe run: `scripts/fi019_test_status.py` on 2026-05-20 (`FA:AB:BC:11:6F:D2`).

Sequence required for successful baseline read:

```
op=(0x00,0x00)  []
op=(0x00,0x01)  [0x00]  -> "FUJIFILM"
op=(0x00,0x01)  [0x01]  -> "FI019"
op=(0x00,0x01)  [0x02]  -> serial
op=(0x00,0x02)  [0x00]  -> 600x800
op=(0x00,0x02)  [0x01]  -> battery
op=(0x00,0x02)  [0x02]  -> photos_left
op=(0x00,0x02)  [0x03]  -> transfers/prints fields
op=(0x00,0x02)  [0x04]  -> camera function payload
op=(0x00,0x02)  [0x05]  -> lifetime shot counter
```

The historical probe helper used at the time attempted `client.pair()` before
subscribe. Current maintained app flow does not treat that as a required
per-session step.

Observed FI019 values in that session:

- model: `FI019`
- image size: `600x800`
- battery: state `2`, pct `12`
- photos_left: `0`
- InfoType `0x03` raw `00030000003c00000010` (decoded: transfers=60, prints=16)
- InfoType `0x04` raw `00040002011d00000000`
- InfoType `0x05` shot counter `67`

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
op=(0x80,0x11)  [0x0B, 0,0,0,0]       reg 0x0B → 1   (Flash ON in 2026-05-21 remote-shooting session)
op=(0x80,0x11)  [0x0C, 0,0,0,0]       → 0   (Film Style OFF candidate)
op=(0x80,0x11)  [0x13, 0,0,0,0]       → 2   (Film Effect #2 / Vivid candidate)
op=(0x80,0x11)  [0x14, 0,0,0,0]       → 1   (Lens Effect #1 / Normal candidate)
op=(0x80,0x11)  [0x15, 0,0,0,0]       → 0   (unknown; possibly another UI setting)
op=(0x80,0x11)  [0x16, 0,0,0,0]       value=0, param=0x2F in fresh session (Exposure 0 candidate; param is not fixed)
op=(0x80,0x11)  [0x17, 0,0,0,0]       → 1   (still used by live HIST/tally logic)
op=(0x80,0x11)  [0x18..0x1A, ...]     → 0   (unknown)
op=(0x80,0x11)  [0x1B, 0,0,0,0]       → 1   (still used by live HIST/tally logic)
op=(0x84,0x00)  []                    HIST_INIT → 13B [00000000 04000000 04000000 00]
op=(0x84,0x01)  [00 00 00 00]         HIST_SCHED → 9B (all zeros)
op=(0x84,0x02)  []                    HIST_COMMIT → [00]
op=(0x84,0x09)  [0x00]                HIST_LIST_REQ slot=00 → 14B
op=(0x84,0x09)  [0x02]                HIST_LIST_REQ slot=02 → 14B
op=(0x84,0x0b)  [0x00]                HIST_SLOT slot=0 → [00 00]
op=(0x84,0x0b)  [0x02]                HIST_SLOT slot=2 → [00 02]
# [if slot 2 count > 0: queue/history pull may use 0x80,0x15 + 0x82,xx]
[app then polls SUPPORT_FUNCTION_INFO InfoTypes 04,05,02,03,01 in rotation every ~0.5s]
```

The `(0x80,0x11)` register sweep is **optional** — the app reads them to mirror
current camera settings in its UI. The fresh 2026-05-21 FI028 remote-shooting
screen aligns with `0x0B`=flash, `0x0C`=film style, `0x13`=film effect,
`0x14`=lens effect, and `0x16`=exposure. Skipping the sweep does not affect any
other functionality. See [registers.md](registers.md) for the current mapping.

## Connection notes

- **Gen 1 (Mini Evo)** may need an OS-level passkey/PIN pairing step on
  Windows. Treat that as a one-time platform prerequisite, not as a required
  Link-protocol opcode step.
- **Gen 2 (Evo Wide)** works in the current app flow without an explicit
  `client.pair()` call.
- The Wide Evo's BLE name ends in `(BLE)` instead of `(IOS)`. Filter on
  service UUID `70954782-…`, not on the advertising name suffix.
