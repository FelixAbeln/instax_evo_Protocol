# Roadmap, gaps, hypotheses & references

← [Wiki index](README.md)

## Local print log

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
| `camera` | BLE address of the camera (Link profile) |
| `model` | Model ID from `DEVICE_INFO_SERVICE` (e.g. `"FI019"`) |
| `transferred` | `true` if image data was fully sent to camera |
| `printed` | `true` if `PRINT_IMAGE` (0x10,0x80) was also sent (film ejected) |
| `photos_left_after` | `photos_left` value from post-print status poll |

`transferred=true, printed=false` means `--enable-print` was not passed — image
was sent but film was not ejected (safe test mode).

## Open hypotheses

Things still plausible but not yet confirmed.

### H2 — Meaning of `CAMERA_FUNCTION_INFO` byte[0] and byte[1]

Normal Wide Evo value: `03 50 00 00 00 00 00 00 00 05 04 01 00 00 00 00`
Keepalive value (different state): `02 32 00 00 00 00 00 00 00 00 00 00 00 00 00 00`

- **byte[1] = 0x32 = 50**: also appears in historical `(0x80,0x15)` response
  payloads on Wide Evo. Could be a capability register, print count, or mode
  identifier.
- **byte[0] = 0x02 vs 0x03**: may encode a camera mode or state-machine state
  (idle=0x02, live-view-active=0x03?).
- **byte[10] = 0x04, byte[11] = 0x01**: stable across samples. Likely
  capability flags.

### H5 — Metadata byte[29] = 0x32

`(0x88,01)` metadata byte[29] = 0x32 = 50. Also appears in
`CAMERA_FUNCTION_INFO` data[1] and in historical `(0x80,0x15)` response byte[8] on
Wide Evo. Possible meanings:
- Total digital transfers made by this camera (lifetime counter)
- A capability/mode register value that is coincidentally the same
- Camera print count (but `CAMERA_HISTORY_INFO` shows different value)

The recurrence of `0x32` across three independent payloads strongly suggests
it is a single firmware-side counter being surfaced through multiple opcodes,
but the specific quantity is still unconfirmed.

### FI019 direct flash write `(0x80,0x11 reg 0x0B)`

Confirmed working on FI028 (see [registers.md](registers.md)); on FI019 the
write completes but no reliable ACK is observed and on-device flash state does
not change consistently. Open whether Gen 1 expects a different `param` byte,
a different register, or requires the change be staged through a higher-level
opcode.


- [javl/InstaxBLE](https://github.com/javl/InstaxBLE) — Python library for
  Instax Link printers (Mini/Square/Wide Link) via Link BLE profile. Protocol
  is structurally identical to what Evo Wide uses.
- [javl/InstaxBLE `Types.py`](https://github.com/javl/InstaxBLE/blob/main/Types.py)
  — EventType and InfoType enumerations
- [javl/InstaxBLE issue #4](https://github.com/javl/InstaxBLE/issues/4#issuecomment-1484123671)
  — Android bugreport HCI capture guide
- [jpwsutton/instax_api](https://github.com/jpwsutton/instax_api) — older
  Wi-Fi-based Instax protocol
