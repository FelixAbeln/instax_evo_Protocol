# TODO (active work list)

← [Wiki index](README.md)

This page tracks near-term, actionable protocol tasks.

## High priority

- Finalize FI028 Film Style naming for selector-02 state byte values:
  - map selector-02 transitions for `00`, `01`, `02`, `05` under controlled toggles
  - include transition captures proving each selector-state behavior
- Add one-command workflow that decodes latest `(88,01)` raw line and appends
  a row to the metadata analysis log
- Add a compact-tail byte-diff view (field-by-field) for quick validation after
  each new transfer

## Medium priority

- Complete `(0x82,0x20)` READY payload semantic mapping beyond
  `total_size`/`chunk_size`
- Validate FI028 metadata mapping stability across reconnects and new sessions
- Add FI019 favorites protocol parity check (`(0x80,0x17)` / `(0x85,xx)`)
  for selector payload behavior and write/read persistence

## Low priority

- Investigate whether any stable camera identity field is exposed during image
  transfer workflows (currently no serial in `(0x88,0x01)` or JPEG EXIF)
- Tighten naming for selector-01 unknown bytes (`b3`, `b6`, `b7`)

## Exit criteria for "favorites + metadata solved"

- Favorites read/write path stable with read-back verification
- `(88,01)` field map documented with confidence notes
- Slot and non-slot captures decode consistently for lens/film/style/state
- Remaining unknowns explicitly scoped and non-blocking
