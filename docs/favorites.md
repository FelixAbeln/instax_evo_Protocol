# Favorites / preset registration

← [Wiki index](README.md)

This page explains the current FI028 favorites protocol behavior in a clean,
implementation-first format.

For raw capture snippets and timestamped wire excerpts, see
[favorites-evidence.md](favorites-evidence.md).

## Scope

- Model: Instax Evo Wide FI028 (Gen 2)
- Surface: Link BLE profile only
- Primary opcodes: 80,17 and 85,00 / 85,01

## Current status

Confirmed:

- Per-slot read works through 80,17 with selector 01 and selector 02.
- Per-slot save works through two 80,17 writes inside an 85 bracket.
- Selector 01 write content round-trips symmetrically in later selector 01 reads.
- Live write and read-back validation is successful in repo tooling.

Still open:

- Exact selector-02 transition semantics for state values 00, 01, 02, 05.
- Final semantics of selector-02 bit0 vs bit2.

## How favorites works

### Read path

For each slot, the app issues two requests:

1. Selector 01 request: 01 00 [slot] 00 00 00 00 00 00 00 00 00
2. Selector 02 request: 02 00 [slot] 00 00 00 00 00 00 00 00 00

Interpretation:

- Selector 01 carries slot content blob and title tail.
- Selector 02 carries per-slot state surface.

### Write path

Each slot write is bracketed and uses two payloads:

1. 85,00 pre
2. 85,01 with 070001000000000000
3. 80,17 Write A (selector 01 content + title)
4. 80,17 Write B (selector 02 state)
5. 85,00 post
6. 85,01 with 070000000000000000

This is the current reproducible save sequence in both app captures and repo
live tests.

## Wire layouts

### Selector 01

Request:

- 01 00 [slot] 00 00 00 00 00 00 00 00 00

Write A:

- 01 02 [slot] 00 [8-byte profile blob] [3-byte title]

Response:

- 00 01 [slot] [occupied] [8-byte blob] [optional title]

### Selector 02

Request:

- 02 00 [slot] 00 00 00 00 00 00 00 00 00

Write B:

- 02 02 [slot] 00 [11-byte state blob]

Response:

- 00 02 [slot] [occupied] [state + body]

## Confirmed field map

### Selector 01 profile blob bytes 4..11

| Byte | Meaning | Confidence |
|---|---|---|
| b0 | Exposure/control byte | high |
| b1 | Lens effect ID | high |
| b2 | Film effect ID | high |
| b3 | Unknown | low |
| b4 | Secondary value/degree byte | medium |
| b5 | White balance ID | high |
| b6 | Unknown | low |
| b7 | Unknown | low |

### Selector 02 state surface

- Byte index 4 is the only moving state byte in current snapshot history.
- In favorites slot snapshots, observed values are: 00, 01, 05.
- In `(88,01)` transfer metadata compact tails, observed values include: 00,
  02, 05.
- Bits currently seen: bit0 and bit2.
- Bytes 5..16 are 00 in all current snapshots.

## Operational limits

### Slot count

- FI028: slots 1..10
- FI019: 3 slots observed on-camera (Mini Evo UI)
- FI019 protocol parity for `(0x80,0x17)` / `(0x85,xx)` favorites read/write is
  still unconfirmed in this repo

### Exposure bounds

- UI range: -2 to +2 EV
- Third-step offsets: -6..+6
- Tooling should reject out-of-range generated exposure writes

## Symmetry summary

Confirmed:

- Selector 01 write content appears unchanged in later selector 01 reads for the
  same slot.

Partially mapped:

- Selector 02 write changes are visible in selector 02 read-back, but value
  semantics are not fully named yet.

## 88,01 compact metadata mapping

When images are pulled through `(88,xx)`, metadata reply `(88,01)` carries a
34-byte payload. Current FI028 mapping is:

- byte 0: status/reserved
- bytes 1..4: JPEG total size (big-endian uint32)
- bytes 5..8: chunk size (big-endian uint32)
- bytes 9..22: timestamp ASCII `YYYYMMDDHHMMSS`
- bytes 23..33: compact settings tail (11 bytes)

Current compact-tail map (provisional but strong):

- `tail[1]` -> selector-01 profile `b0` (exposure/control byte)
- `tail[2]` -> selector-01 profile `b1` (lens effect ID)
- `tail[3]` -> selector-01 profile `b2` (film effect ID)
- `tail[4]` -> selector-02 moving state byte
- `tail[6]` -> selector-01 profile `b4` (secondary value/degree)
- `tail[10]` -> selector-01 profile `b5` (white balance ID)

Notes:

- Slot 04 and slot 09 still collide in this compact encoding under current
  captured settings.
- Some compact tail bytes still look reserved/unknown.
- Confirmed non-slot sample: `raw=0000034fca0000261532303236303632323139333631300001070702003201000003`
  decodes to lens effect `07`, film effect `07`, selector-02 state `02`.

## Validated live tests

Two repo-side write/read-back tests are confirmed:

1. Slot 4 style-on write and read-back
2. Slot 1 full-default write and read-back

Raw payloads and before/after values are tracked in
[favorites-evidence.md](favorites-evidence.md).

## Implementation notes

### Build writes

Use scripts/favorites_slot_codec.py build-write to generate Write A and Write B
payloads per slot.

### Read and diff

Use scripts/favorites_live_slots.py dump and diff to capture and compare
selector 01 and selector 02 changes.

## Known unknowns

- Semantics of b3, b6, b7 in selector 01 blob
- Exact selector-02 transition semantics for style state values
- Full operation matrix for 85,01 leading mode byte across all app flows

## Evidence and references

- [favorites-evidence.md](favorites-evidence.md)
- [effects-by-model.md](effects-by-model.md)
- [roadmap.md](roadmap.md)
- captures/analysis/favorites_compact_metadata_decode_2026-05-22.md
- captures/favorites/flows/slot2_save_flow_sanitized.txt
- captures/favorites/flows/slot3_save_flow_sanitized.txt
- captures/favorites/snapshots/favorites_slots_20260522_165345.json
