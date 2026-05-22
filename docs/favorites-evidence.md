# Favorites evidence and capture log

← [Wiki index](README.md)

This page stores raw favorites evidence so [favorites.md](favorites.md) can stay
focused on explanation and implementation.

## Capture set used

| ID | File | Purpose |
|---|---|---|
| FAV-LOG-01 | captures/favorites/flows/slot2_save_flow_sanitized.txt | First observed save bracket, slot 2 write |
| FAV-LOG-02 | captures/favorites/flows/slot3_save_flow_sanitized.txt | Second observed save bracket, slot 3 write |
| FAV-SNAP-01 | captures/favorites/snapshots/favorites_slots_20260522_161757.json | 4-slot selector-01 compare baseline |
| FAV-SNAP-02 | captures/favorites/snapshots/favorites_slots_20260522_165345.json | 4-slot selector-01 plus selector-02 contradiction pull |
| FAV-META-01 | captures/analysis/favorites_compact_metadata_decode_2026-05-22.md | `(88,01)` compact metadata decode table and slot-family mapping |
| FAV-META-02 | transfer_2026-06-22_193610_1779474813.jpg + raw line | Non-slot compact metadata sample (lens=07 film=07 state=02) |

Sanitization metadata:

- captures/README.md
- captures/sanitization_report.json

## Raw flow: slot 2 save (FAV-LOG-01)

```
482.12  W (85,00)
482.15  N (85,00) 0000ff0000
482.15  W (85,01) 070001000000000000
482.18  N (85,01) 00

482.18  W (80,17) 01020200840107000e000303616263
482.21  N (80,17) 000102000000000000000000

482.21  W (80,17) 0202020001000000000000000000
482.24  N (80,17) 0002020000000000000000000000000000

482.24  W (85,00)
482.27  N (85,00) 0000ff0000
482.27  W (85,01) 070000000000000000
482.30  N (85,01) 00
```

## Raw flow: slot 3 save (FAV-LOG-02)

```
1468.09  W (85,00)
1468.12  N (85,00) 0000ff0000
1468.12  W (85,01) 070001000000000000
1468.15  N (85,01) 00

1468.15  W (80,17) 01020300840107000e0003037a7a7a
1468.18  N (80,17) 000103000000000000000000

1468.18  W (80,17) 0202030001000000000000000000
1468.21  N (80,17) 0002030000000000000000000000000000

1468.21  W (85,00)
1468.24  N (85,00) 0000ff0000
1468.24  W (85,01) 070000000000000000
1468.27  N (85,01) 00
```

## Live validation runs (2026-05-22)

### Run A: slot 4 style-on write

Write payloads:

- Write A: 01020400840909004b060000543234
- Write B: 020204000500000000000000000000

Read-back:

- Before selector-01: 00010401000909004b060000
- After selector-01: 00010401840909004b060000
- Before selector-02: 0002040101000000000000000000000000
- After selector-02: 0002040105000000000000000000000000

### Run B: slot 1 full-default write

Write payloads:

- Write A: 010201000000000032000000444546
- Write B: 020201000000000000000000000000

Read-back:

- Before selector-01: 00010101000909004b060000
- After selector-01: 000101010000000032000000
- Before selector-02: 0002010101000000000000000000000000
- After selector-02: 0002010100000000000000000000000000

## Selector-02 byte audit summary

From all current favorites snapshot files:

- Byte index 4 is the only moving state byte.
- Observed state values: 00, 01, 05.
- Bytes 5..16 remain 00 in all current snapshots.
- Non-zero-capable structural bytes are:
  - byte 1 (selector echo = 02)
  - byte 2 (slot index)
  - byte 3 (occupancy flag)

From `(88,01)` compact metadata tails:

- Observed state values include: 00, 02, 05.
- Sample raw with state 02:
  `0000034fca0000261532303236303632323139333631300001070702003201000003`

## Notes

- Keep this page for raw capture snippets and IDs.
- Keep [favorites.md](favorites.md) for field map and implementation guidance.

## 88,01 compact metadata summary (FAV-META-01)

From live FI028 transfer captures in one session:

- `(88,01)` has 34 bytes total.
- Bytes 9..22 are timestamp ASCII `YYYYMMDDHHMMSS`.
- Compact tail bytes 23..33 map closely to selector-01 profile fields and
  selector-02 moving state byte.
- Distinct tail families were observed for:
  - slot-01/default-like
  - slot-04/09-like
  - slot-10-like
