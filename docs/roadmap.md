# Roadmap, gaps, hypotheses & references

← [Wiki index](README.md)

Capture-backed supporting material is organized in [evidence.md](evidence.md).

## Active investigation items

Open hypotheses plus partially resolved topics that still need semantic/parity closure.

## Status snapshot (2026-05-22)

- FI028 favorites wire path is stable and reproducible: read + save + read-back.
- `(0x88,0x01)` compact metadata is mostly decoded for FI028 and now maps to
  lens/film/state fields with high confidence.
- Remaining work is now mostly semantic naming and cross-model parity, not
  wire-shape discovery.

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

### H6 — Favorites semantics and cross-model parity (wire path resolved)

Status update: FI028 wire path is mapped and reproducible in this repo; remaining work is semantic labeling and FI019/Gen 3 parity.

Confirmed on FI028:
- Per-slot read surface is `(0x80,0x17)` with selector `0x01` and selector
  `0x02` requests.
- Save path is a bracketed sequence using `(0x85,0x00)` and `(0x85,0x01)` around
  two `(0x80,0x17)` writes:
  - Write A: selector `0x01` content blob + 3-byte title.
  - Write B: selector `0x02` state blob.
- Write/read symmetry is confirmed for selector `0x01` payload content.
- Live write tests in this repo successfully changed slot state and read back
  persisted values (including a full-default slot profile write).

What is still open:
- Full semantic mapping of selector `0x02` byte `4` state transitions
  (`0x00`, `0x01`, `0x02`, `0x05`) under controlled toggles.
- Semantics of selector `0x02` bit0 vs bit2 across all style transitions.
- Complete model parity check for FI019 favorites payload behavior
  (`(0x80,0x17)` / `(0x85,xx)`) and future Gen 3 parity once hardware is available.
  Slot count itself is now observed as 3 on Mini Evo UI.

Narrow capture plan (remaining work):
1. Hold selector-`0x01` bytes fixed and force selector-`0x02` transitions on a
   single slot.
2. Capture controlled transitions `0x00 <-> 0x01` and `0x01 <-> 0x05`.
3. Record UI-visible style name for each transition and update
   [favorites.md](favorites.md) + [effects-by-model.md](effects-by-model.md).

Done criteria:
- Selector-`0x02` state-transition behavior documented for all observed values.
- Repro script that writes style state by name and verifies read-back.

### H7 — Decode Gen 2 image metadata payloads (mostly resolved for `(0x88,0x01)`)

Working assumption: FI028 returns stable metadata blocks during image-transfer
flows, but field-level meaning is only partially decoded in this repo.

Priority targets:
- `(0x82,0x20)` READY payload fields beyond `total_size` and `chunk_size`.
- Cross-session stability checks for `(0x88,0x01)` compact-tail semantics.
- Any repeated counters/timestamps that correlate with shot index or camera
  state across mixed capture modes.

What is already confirmed:
- `(0x88,0x01)` is 34 bytes.
- Byte layout for size/chunk/timestamp is stable and decoded.
- Compact tail maps strongly to favorites-related fields (lens/film/state +
  additional profile bytes), including non-slot live states.

What we still need to confirm:
- Which compact-tail bytes are pure transport markers vs camera semantic state.
- Cross-flow consistency between `(0x88,0x01)` and `(0x82,0x20/0x21)` metadata.
- Whether any metadata field encodes stable camera identity (currently no serial
  field evidence in `(0x88,0x01)` or JPEG payload metadata).

Capture plan:
1. Collect 6 to 10 additional FI028 transfers where only one control changes at
  a time (lens only, film only, style/state only, WB only).
2. Repeat one known setting pair across separate reconnect sessions to confirm
  compact-tail stability.
3. Add at least one `(0x82,0x20)`-focused capture set and perform side-by-side
  diffs against `(0x88,0x01)` rows.

Done criteria:
- A documented field map in docs with offsets, sizes, and confidence level.
- Decoder code that outputs structured metadata for FI028 transfers.
- Independent validation run where decoded fields match observed capture
  conditions for both slot-based and non-slot live states.
- `(0x82,0x20)` supplemental fields documented or explicitly ruled non-semantic.

### FI019 direct flash write `(0x80,0x11 reg 0x0B)`

Confirmed working on FI028 (see [registers.md](registers.md)); on FI019 the
write completes but no reliable ACK is observed and on-device flash state does
not change consistently. Open whether Gen 1 expects a different `param` byte,
a different register, or requires the change be staged through a higher-level
opcode.

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
