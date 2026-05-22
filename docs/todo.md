# TODO (active work list)

← [Wiki index](README.md)

This page tracks near-term, actionable protocol tasks.

## High priority

- Finalize FI028 selector-02 state-transition semantics:
  - map selector-02 transitions for `00`, `01`, `02`, `05` under controlled toggles
  - include transition captures proving each selector-state behavior
- Add one-command workflow that decodes latest `(88,01)` raw line and appends
  a row to the metadata analysis log
- Add a compact-tail byte-diff view (field-by-field) for quick validation after
  each new transfer
- Recover official app queue-state machine from archived traces:
  - map `(0x00,0x02)` sub-`0x04` profile families (`0105`, `0232`, `0350/035f`, `0b50`)
    and identify which byte(s) represent remaining entries vs transfer phase
  - validate FI028 `0b50` hypothesis: `b13` behaves as remaining-entry countdown
    in Share flow (`2 -> 1 -> 0` observed in current live run)
  - keep `(84,09)` idx `00/02` as supplemental evidence, not primary depth source

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
