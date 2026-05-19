# Roadmap, gaps, hypotheses & references

← [Wiki index](README.md)

## Known gaps — commands not yet identified

### Print history / transferred-images gallery — RESOLVED

The camera automatically registers every ejected print in its internal history
(up to 50 entries, stored in camera flash) regardless of which BLE client
triggered the print. Confirmed 2026-05-17: prints sent via our tool are visible
in the on-camera print history without any additional commands.

The Instax app's "TRANSFERRED IMAGES" gallery is populated by a separate
user-initiated flow: the user selects "PRINTED IMAGE TRANSFER" from the
camera's physical menu, which causes the camera to push the stored JPEG back
over BLE to the app. That read-back transfer is not part of the print
pipeline.

## Remote shutter — open

**Goal:** Trigger the camera's shutter remotely over BLE.

Now that `(0x82,xx)` is confirmed as live view, the remote shutter is the next
thing to reverse-engineer. Candidates:

- `CAMERA_SETTINGS` (op1=0x80, op2=0x00) — write a "capture" setting
- `CAMERA_SETTINGS_GET` (0x80,0x01) — poll for shutter-ready state
- An undiscovered opcode (capture a BLE session during app remote-shutter use)

**What we need:** Capture an HCI log while using the Instax app's
remote-shutter feature and look for the trigger opcode.

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

## Hypotheses & open questions

This section tracks theories and ideas that are plausible but not yet
confirmed.

### H1 — Why Mini Evo (Gen 1) does not respond to `(0x88,00)`

**Observed:** Connected to Mini Evo, MTU=247, subscribed OK. Sent `(0x88,00)`
— camera sent nothing back in 20 s.

**Theory A (most likely):** The image_receive script never polled
`CAMERA_FUNCTION_INFO` first. The camera may require seeing the polling loop
(simulating the real app's keepalive) before it recognises the session as
legitimate. → *Test: add `(0x00,00)` hello + `(0x00,02)` InfoType=0x04 polling
loop before `(0x88,00)`.*

**Theory B:** Gen 1 (FI019) and Gen 2 (FI028) have different opcodes for image
transfer. Gen 2 uses `0x88`; Gen 1 may use `0x86` or another family that was
not in the available btsnoop captures. → *Test: capture an HCI log of the
Instax app doing an image transfer from the Mini Evo.*

**Theory C:** The transfer-ready flag is in a different InfoType on Gen 1.
→ *Test: poll all (0x00,02) InfoTypes while pressing Share; compare payload
snapshots.*

**Theory D:** Mini Evo firmware update (2026) changed the pairing/bonding flow
and may also have changed or added the 0x88 protocol.

### H2 — Meaning of `CAMERA_FUNCTION_INFO` byte[0] and byte[1]

Normal Wide Evo value: `03 50 00 00 00 00 00 00 00 05 04 01 00 00 00 00`
Keepalive value (different state): `02 32 00 00 00 00 00 00 00 00 00 00 00 00 00 00`

- **byte[1] = 0x32 = 50**: also appears in `LIVE_VIEW_PREPARE` response
  byte[8] on Wide Evo. Could be a capability register, print count, or mode
  identifier.
- **byte[0] = 0x02 vs 0x03**: may encode a camera mode or state-machine state
  (idle=0x02, live-view-active=0x03?).
- **byte[10] = 0x04, byte[11] = 0x01**: stable across samples. Likely
  capability flags.

### H3 — Correct polling loop before `(0x88,00)` (Gen 1)

The btsnoop shows the Wide Evo app sends a full session handshake before
polling begins: `(0x00,00)` hello → device info queries → `(0x00,02)`
InfoType=0x04/05/02/03/01 rotation. Our current scripts skip the handshake
and go straight to `(0x88,00)`. Gen 1 may require the handshake to initialise
internal session state.

### H4 — `(0x88,02)` chunk ACK vs data: two frames or one?

The btsnoop appeared to show two `(0x88,02)` cam→phone responses per request
(a 4B ACK then the large data frame). Live testing confirmed it is actually
**one frame**; the `[img_idx:4]` prefix was being misparsed as a separate ACK.
See [image-pull.md](image-pull.md).

### H5 — Metadata byte[29] = 0x32

`(0x88,01)` metadata byte[29] = 0x32 = 50. Also appears in
`CAMERA_FUNCTION_INFO` data[1] and in `LIVE_VIEW_PREPARE` response byte[8] on
Wide Evo. Possible meanings:
- Total digital transfers made by this camera (lifetime counter)
- A capability/mode register value that is coincidentally the same
- Camera print count (but `CAMERA_HISTORY_INFO` shows different value)

## References

- [javl/InstaxBLE](https://github.com/javl/InstaxBLE) — Python library for
  Instax Link printers (Mini/Square/Wide Link) via Link BLE profile. Protocol
  is structurally identical to what Evo Wide uses.
- [javl/InstaxBLE `Types.py`](https://github.com/javl/InstaxBLE/blob/main/Types.py)
  — EventType and InfoType enumerations
- [javl/InstaxBLE issue #4](https://github.com/javl/InstaxBLE/issues/4#issuecomment-1484123671)
  — Android bugreport HCI capture guide
- [jpwsutton/instax_api](https://github.com/jpwsutton/instax_api) — older
  Wi-Fi-based Instax protocol

## Legacy archive

The original 2 600-line monolithic protocol notebook is preserved at
[protocol-legacy.md](protocol-legacy.md). As of 2026-05-19 every concrete
piece of information it contained has been ported into the topical wiki pages
— the legacy file is now redundant and safe to delete.
