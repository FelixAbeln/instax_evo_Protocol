# Compact 88,01 Metadata vs Favorites Snapshot (2026-05-22)

Source: live GUI console transcript during the same BLE session, with post-transfer favorites snapshots enabled.

## Favorites signatures used for matching

- Slot 01 signature:
  - sel01 profile bytes: `00 00 00 00 32 00 00 00`
  - sel02 moving byte: `00`
- Slot 04/09 signature (same profile):
  - sel01 profile bytes: `84 09 09 00 4b 06 00 00`
  - sel02 moving byte: `05`
- Slot 10 signature:
  - sel01 profile bytes: `04 09 09 00 4b 06 00 00`
  - sel02 moving byte: `05`

## Observed transfers

| Saved image | 88,01 timestamp | Size (bytes) | Raw 88,01 | Compact tail (last 11 bytes) | Nearest favorite signature |
|---|---:|---:|---|---|---|
| transfer_2026-06-22_185434_1779473239.jpg | 2026-06-22 18:54:34 | 214862 | 000003474e0000261532303236303632323138353433340000000000003201000000 | 0000000000003201000000 | Slot 01 / default-like |
| transfer_2026-06-22_191356_1779473456.jpg | 2026-06-22 19:13:56 | 163818 | 0000027fea0000261532303236303632323139313335360000000000003201000000 | 0000000000003201000000 | Slot 01 / default-like |
| transfer_2026-06-22_191731_1779473674.jpg | 2026-06-22 19:17:31 | 163707 | 0000027f7b0000261532303236303632323139313733310000000000003201000000 | 0000000000003201000000 | Slot 01 / default-like |
| transfer_2026-06-22_191842_1779473737.jpg | 2026-06-22 19:18:42 | 141699 | 00000229830000261532303236303632323139313834320084090905004b01000006 | 0084090905004b01000006 | Slot 04 or Slot 09 family |
| transfer_2026-06-22_192212_1779473941.jpg | 2026-06-22 19:22:12 | 140925 | 000002267d0000261532303236303632323139323231320004090905004b01000006 | 0004090905004b01000006 | Slot 10 family |
| transfer_2026-06-22_192053_1779473954.jpg | 2026-06-22 19:20:53 | 139444 | 00000220b40000261532303236303632323139323035330084090905004b01000006 | 0084090905004b01000006 | Slot 04 or Slot 09 family |
| transfer_2026-06-22_193610_1779474813.jpg | 2026-06-22 19:36:10 | 217034 | 0000034fca0000261532303236303632323139333631300001070702003201000003 | 0001070702003201000003 | Non-slot live state (lens=07 film=07 state=02) |

## What is confirmed

- 88,01 includes date+time as ASCII `YYYYMMDDHHMMSS`.
- The trailing compact block changes with favorites-linked settings.
- The compact block distinguishes at least:
  - default/slot-01-like (`...0000000000003201000000`)
  - slot-04/09-like (`...0084090905004b01000006`)
  - slot-10-like (`...0004090905004b01000006`)

## Current uncertainty

- Slot 04 vs slot 09 cannot be disambiguated from this compact block alone in this dataset (same signature family observed).
- Exact byte semantics inside the compact tail are still partly inferred (relationship is strong, full naming pending).

## Inferred 88,01 layout

Observed raw length is 34 bytes (`68` hex chars).

- Byte `0`: status/reserved (always `00` in this dataset)
- Bytes `1..4`: JPEG total size (big-endian uint32)
- Bytes `5..8`: transfer chunk size (big-endian uint32)
- Bytes `9..22`: ASCII timestamp `YYYYMMDDHHMMSS`
- Bytes `23..33`: compact settings tail (11 bytes)

## Inferred compact tail field map (bytes 23..33)

Tail bytes are shown as `t0..t10` where `t0` is raw byte 23.

| Tail byte | Typical values seen | Best current meaning | Confidence |
|---|---|---|---|
| `t0` | `00` | reserved / unknown | low |
| `t1` | `00`, `04`, `84` | selector-01 profile `b0` (exposure/control byte) | high |
| `t2` | `00`, `09` | selector-01 profile `b1` (lens effect ID) | high |
| `t3` | `00`, `09` | selector-01 profile `b2` (film effect ID) | high |
| `t4` | `00`, `05` | selector-02 moving state byte | high |
| `t5` | `00` | reserved / unknown | low |
| `t6` | `32`, `4b` | selector-01 profile `b4` (secondary value/degree) | high |
| `t7` | `01` | constant marker / occupied flag surrogate | medium |
| `t8` | `00` | reserved / unknown | low |
| `t9` | `00` | reserved / unknown | low |
| `t10` | `00`, `06` | selector-01 profile `b5` (white balance ID) | high |

Note: newer live sample adds values `t2=t3=07`, `t4=02`, `t10=03`, confirming
the same byte positions continue to track lens/film/state/WB in non-slot states.

## Cross-check examples

- Default-like (slot 01 family):
  - tail: `00 00 00 00 00 00 32 01 00 00 00`
  - aligns with sel01 profile `00 00 00 00 32 00 ...` and sel02 state `00`

- Slot 04/09 family:
  - tail: `00 84 09 09 05 00 4b 01 00 00 06`
  - aligns with sel01 profile `84 09 09 00 4b 06 ...` and sel02 state `05`

- Slot 10 family:
  - tail: `00 04 09 09 05 00 4b 01 00 00 06`
  - aligns with sel01 profile `04 09 09 00 4b 06 ...` and sel02 state `05`

## Requested check: image 3 and image 4

Using transfer order from this session table:

- Image 3 = `transfer_2026-06-22_191731_1779473674.jpg`
  - raw tail: `0000000000003201000000`
  - mapping: Slot 01 / default-like
  - reason: exact match to the Slot-01-family compact tail seen in images 1 and 2.

- Image 4 = `transfer_2026-06-22_191842_1779473737.jpg`
  - raw tail: `0084090905004b01000006`
  - mapping: Slot 04 or Slot 09 family
  - reason: exact match to the shared Slot-04/09 signature family.

### Duplicate concern (verified)

SHA256 shows one exact duplicate pair in the transfer folder:

- `transfer_2026-06-22_185434_1779473047.jpg`
- `transfer_2026-06-22_185434_1779473239.jpg`

Both hashes are identical (`9E45556934B12519ABF5C6BA5DFFA3B451DEBA6860C54568916FDB07E73CCCA3`).

Interpretation:

- Yes, at least one image was transferred twice (byte-identical).
- This duplicate does not affect image 3/4 mapping above; those files have distinct hashes.
