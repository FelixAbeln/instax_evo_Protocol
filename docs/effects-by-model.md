# Effects And Styles By Model

← [Wiki index](README.md)

For raw ID extraction evidence and capture references, use [evidence.md](evidence.md).

This page centralizes user-facing effect/style names and known numeric IDs by
camera model. Keep protocol mechanics in other pages (for example
[history-log.md](history-log.md) and [favorites.md](favorites.md)) and keep the
name catalogs here.

## Scope and terminology

- "Film Effect" in this page means the shooting effect name catalog for a
  given model.
- "Lens Effect" means the lens-effect name catalog for a given model.
- "Film Style" means favorites/profile style labeling in capture metadata.

Wire-level read/write paths and register details are documented in
[history-log.md](history-log.md), [registers.md](registers.md), and
[favorites.md](favorites.md).

## FI028 (Instax Wide Evo)

### Film Effect IDs

These names are already validated in the history mapping workflow.

| ID | Film Effect name |
|---|---|
| 1 | Normal |
| 2 | Vivid |
| 3 | Warm |
| 4 | Sky Blue |
| 5 | Light Green |
| 6 | Magenta |
| 7 | Sepia |
| 8 | Monochrome |
| 9 | Amber |
| 10 | Summer |

### Lens Effect IDs

| ID | Lens Effect name |
|---|---|
| 1 | Normal |
| 2 | Light Leak |
| 3 | Light Prism |
| 4 | Vignette |
| 5 | Soft Glow |
| 6 | Double Ex. |
| 7 | Color Shift |
| 8 | Monochrome Blur |
| 9 | Color Gradient |
| 10 | Beam Flare |

### Film Style names (favorites/profile context)

Status update (operator-confirmed in live decoding): Film Style names follow
the Film Effect catalog order for FI028.

| Style ID | Style name (same as Film Effect order) | Status |
|---:|---|---|
| 1 | Normal | user-confirmed ordering |
| 2 | Vivid | user-confirmed ordering |
| 3 | Warm | user-confirmed ordering |
| 4 | Sky Blue | user-confirmed ordering |
| 5 | Light Green | user-confirmed ordering |
| 6 | Magenta | user-confirmed ordering |
| 7 | Sepia | user-confirmed ordering |
| 8 | Monochrome | user-confirmed ordering |
| 9 | Amber | user-confirmed ordering |
| 10 | Summer | user-confirmed ordering |

Current working hypothesis (selector-`0x02` state byte):

| State value | Hypothesis status |
|---:|---|
| `0` | style OFF / no style override (high confidence) |
| `1` | style enabled family (exact selector-state semantics pending) |
| `2` | style enabled family (observed in non-slot metadata) |
| `3` | style enabled family (unobserved) |
| `4` | style enabled family (unobserved) |
| `5` | style enabled family (observed in slot captures) |

Notes:

- This table is intentionally provisional and based on selector-`0x02` state
  observations from favorites snapshots.
- Observed values so far are `0`, `1`, `2`, and `5`; value `2` is confirmed in
  non-slot `(88,01)` metadata.
- Values `3..4` remain placeholders until captured.
- Slot-2 audit is especially strong: only selector-`0x02` byte `4` changes
  (`0x01 <-> 0x05`), and bytes `5..16` stay `0x00` across snapshots.
- Exact selector-`0x02` transition semantics are still pending controlled
  captures.

### White Balance modes (favorites/profile context)

Source: FI028 manual (section "Types of White Balance").

| Manual order | White Balance mode | Favorites blob status |
|---|---|---|
| 0 | AUTO | observed as `b5=0x00` |
| 1 | FINE | ID pending capture |
| 2 | SHADE | ID pending capture |
| 3 | FLUORESCENT LIGHT-1 | ID pending capture |
| 4 | FLUORESCENT LIGHT-2 | ID pending capture |
| 5 | FLUORESCENT LIGHT-3 | ID pending capture |
| 6 | INCANDESCENT | ID pending capture |

Known profile-blob evidence from [favorites.md](favorites.md):

- `00 00 00 00 32 00 03 03` corresponds to card values including
  Lens=Normal, Film=Normal, Style=OFF.
- `84 01 07 00 0e 00 03 03` corresponds to card values including
  Lens=Light Leak, Film=Monochrome, with style still unresolved at byte level.

Current provisional FI028 decode in favorites profile blob (`b0..b7`):

- `b0`: exposure control byte (high-confidence from one-byte transitions)
- `b1`: lens effect ID (`0x00` Normal, `0x01` Light Leak)
- `b2`: film effect ID (`0x00` Normal, `0x07` Monochrome)
- `b5`: white balance (`0x00` AUTO in observed cases)
- `b4`: secondary value/degree byte (direct values observed)
- `b3`, `b6`, `b7`: unknown

Live confirmation from slot diff (2026-05-22):

- Lens Beam Flare mapped to `b1=0x09`.
- Film Summer mapped to `b2=0x09`.
- White Balance INCANDESCENT mapped to `b5=0x06`.
- Exposure-control byte observations include `b0=0x04`, `0x84`, and `0x01` in
  recent captures.

White Balance note:

- Manual mode names are now cataloged above.
- Only AUTO (`b5=0x00`) is currently observed in captures.
- Remaining WB numeric IDs need one capture per mode to confirm mapping.

Confirmed WB ID points so far:

- `0x00` = AUTO
- `0x06` = INCANDESCENT

Value note (favorites profile blob byte `b4`):

- Confirmed direct value points: `0x19 => 25`, `0x32 => 50`, `0x4b => 75`.
- In captured Film Strip profile updates, changing only the UI degree changed
  only byte `b4` to the same numeric value.
- Film Style naming/order is now treated as known for FI028 (same order as Film
  Effect IDs). Remaining open items are selector-state transition semantics in
  [favorites.md](favorites.md).

In favorites profile blobs, your finding is confirmed so far:

- Normal lens and Normal film both encode as `0x00`.

## FI019 (Instax Mini Evo)

User-provided FI019 effect catalog (Mini Evo):

### Lens Effect IDs

| ID | Lens Effect name |
|---|---|
| 1 | Normal |
| 2 | Vignette |
| 3 | Soft Focus |
| 4 | Blur |
| 5 | Fisheye |
| 6 | Color Shift |
| 7 | Light Leak |
| 8 | Mirror |
| 9 | Double Exposure |
| 10 | Half Frame |

### Film Effect IDs

| ID | Film Effect name |
|---|---|
| 1 | Normal |
| 2 | Vivid |
| 3 | Pale |
| 4 | Canvas |
| 5 | Monochrome |
| 6 | Sepia |
| 7 | Yellow |
| 8 | Red |
| 9 | Blue |
| 10 | Retro |

### Film Types

- No separate film-type catalog has been found on this FI019 camera so far.

Observed operational note:

- Favorites slot count on camera UI appears to be 3.
- Link-protocol parity for favorites commands (`(0x80,0x17)` / `(0x85,xx)`) is
  still unconfirmed in this repo.

## Gen 3 (Mini Evo Cinema, model ID unknown)

No confirmed catalog yet. Keep separate from FI019/FI028 to avoid mixing names
across models.

## Mapping policy

- Keep each model in its own section.
- Mark rows as observed, provisional, or pending.
- Do not assume names/IDs are shared across models unless verified by capture
  or manual evidence for that specific model.
