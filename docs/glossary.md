# Glossary & notation

← [Wiki index](README.md)

Terms, abbreviations, and notation conventions used across this wiki.

## Notation

| Form | Meaning |
|---|---|
| `(op1, op2)` | A Link opcode pair, e.g. `(0x82, 0x01)`. `op1` selects a family, `op2` selects an action inside it. |
| `[name:N]` inside a payload | An `N`-byte field. All multi-byte integers are **big-endian** unless noted. |
| `payload` | The bytes between `op2` and the trailing checksum byte in a Link frame — i.e. what `recv_frame()` returns as its third element. |
| `chunk` | One slice of a larger image being transferred in either direction. Distinct from "ATT notification" (one BLE packet) and "frame" (one complete Link request/response). |
| FI019 / FI028 | The internal model IDs returned by `DEVICE_INFO_SERVICE` InfoType=`0x01`. Mini Evo = FI019 (Gen 1); Evo Wide = FI028 (Gen 2). |
| Gen 1 / Gen 2 | Same as FI019 / FI028. Gen 3 ≈ Evo Square (assumed FI029+, untested). |

## Roles

| Term | Meaning |
|---|---|
| **Link profile** | The shared BLE protocol used by Instax Mini Link, Square Link, Wide Link, Mini Evo, Evo Wide, and (assumed) Evo Square. This is the protocol documented here. |
| **Android profile** | The legacy companion protocol used by the Instax Mini Evo Android app over a Fujifilm-vendor secondary service. Documented historically in [android-legacy.md](android-legacy.md). Out of scope for this wiki. |
| **Phone** | Any Link client — phone, tablet, desktop, or this library. |
| **Camera** | The Instax device. Both printer-only and photo-capable models speak the same protocol. |

## Protocol & framing

| Term | Meaning |
|---|---|
| **Frame** | One complete Link request or response packet: `41 62 [len:2][op1][op2][payload][cs]` (request) or `61 42 …` (response). |
| **Checksum (cs)** | One byte, chosen so that `(sum(packet) & 0xFF) == 0xFF`. |
| **ATT notification** | One BLE notify packet, at most `MTU − 3` bytes. Large frames span several. |
| **CCCD** | Client Characteristic Configuration Descriptor — the standard BLE handle (`0x2902`) you write `01 00` to enable notifications. `bleak.start_notify()` does this for you. |
| **MTU** | Maximum Transmission Unit on the BLE link. 247 = bonded, 23 = unbonded — see [implementation.md](implementation.md). |
| **Sequence counter** | A monotonically increasing number some clients embed in requests. Not validated by the camera; safe to omit. The counter is **global across BLE sessions** — the camera does not require it to reset on reconnect. |

## Identity & status

| Term | Meaning |
|---|---|
| **InfoType** | The 1-byte selector used by `(0x00,0x01)` (string fields) and `(0x00,0x02)` (numeric fields) to choose which piece of identity/status to read. Full table in [link-protocol.md](link-protocol.md#info-types). |
| **`CAMERA_FUNCTION_INFO`** | The response of `(0x00,0x02,[0x04])`. A 16-byte status block — battery, capability flags, **transfer-ready flag** at `data[2]`, mode hints. See [image-pull.md](image-pull.md). |
| **Transfer-ready flag** | `CAMERA_FUNCTION_INFO[data[2]]` — non-zero means a photo is queued and ready to be pulled with `(0x88,xx)`. |
| **`photos_left`** | Low nibble of `(0x00,0x02,[0x02])` payload byte `data[0]`. Number of unexposed sheets remaining in the cartridge (0–10). |

## Image flows

| Term | Meaning |
|---|---|
| **Print pipeline** | `(0x10,00) START` → `(0x10,01) DATA × N` → `(0x10,02) END` → `(0x10,80) PRINT`. See [print.md](print.md). |
| **Live view** | The phone-pulled viewfinder stream: `(0x82,00)` open, `(0x82,01)` pull frame, `(0x82,02)` close. See [live-view.md](live-view.md). |
| **Slot** | The 1-byte argument to `(0x82,00)` and `(0x82,02)`. Always `0x00` in current firmware; reserved. |
| **Auto-transfer** | The post-shutter download flow `(0x82, 10/20/21/22)`. The camera-side equivalent of share-button pull. See [auto-transfer.md](auto-transfer.md). |
| **Share-button pull** | The user-initiated photo download triggered by the camera's Share button: `(0x85,xx)` prepare → poll transfer-ready → `(0x88,xx)` drain. FI028 only. See [image-pull.md](image-pull.md). |
| **Queue-transfer / QUE-button drain** | The bulk-download initiated by the camera's QUE physical button: `(0x84,xx)` → `(0x80,15)` → `(0x82,xx)`. See [queue-transfer.md](queue-transfer.md). |

## History (HIST)

| Term | Meaning |
|---|---|
| **HIST** | "History" — the camera-side tally buffer of how many shots have been taken per (film, lens, effect) combination. Read via the `(0x84,xx)` family. Decoder + byte map in [history-log.md](history-log.md). |
| **HIST table** | A 37 × 44-byte matrix returned by `(0x84,0x02)`. Each row is one film type; each row maps lens/effect tallies. |
| **`HIST_DONE`** | `(0x84,0x0b)` — **acknowledges** the HIST buffer and **clears it on the camera**. Send this only after you have safely stored the buffer phone-side; otherwise the counters are lost. |
| **Live HIST** | The live shot-counter byte read via `(0x00,0x02,[0x05])`, which increments in real time as the user takes pictures. Use this for runtime counters; use the HIST table for historical totals. |
| **Tally register** | Registers `0x17` (Wide) and `0x1B` (Mini) inside `(0x80,11)` — same data as live HIST, exposed through the register interface. |

## Settings & registers

| Term | Meaning |
|---|---|
| **Register / reg** | A numbered camera-side setting accessible via `(0x80,0x11)`. Full table in [registers.md](registers.md). |
| **`param` byte** | Second byte of a `(0x80,0x11)` write payload. Meaning varies per register; documented inline where known. |
| **Film Effect** | A per-film post-process applied at print time. Distinct from **Film Style** (lens-side filter applied at capture). Both have separate registers. |
| **DE 2-press cycle** | The Mini Evo's physical "DE" dial behaviour where a double-press cycles a setting. Mentioned in [model-quirks.md](model-quirks.md). |

## Advertising & discovery

| Term | Meaning |
|---|---|
| **`(IOS)` suffix** | Appears on Mini Evo advertising names: `INSTAX-<serial> (IOS)`. Historical; not a real iOS-vs-Android selector. |
| **`(BLE)` suffix** | Appears on Evo Wide advertising names: `INSTAX-<serial>(BLE)`. Same protocol. |
| **Service-UUID filter** | The correct way to discover Evo cameras — match `70954782-2d83-473d-9e5f-81e1d02d5273` in the advertisement, ignore the name suffix. |

## Implementation terms

| Term | Meaning |
|---|---|
| **`recv_frame`** | The reassembler shown in [quickstart.md § 3](quickstart.md#3-frame-reassembly-helper-used-by-every-flow). Reads ATT notifications from a queue until one complete Link frame is assembled. Used by every documented flow. |
| **`make_packet`** | The packet builder shown in [quickstart.md § 2](quickstart.md#2-packet-framing). Pairs with `recv_frame`. (Older notes may say `create_packet` — same thing.) |
| **`_ble_busy`** | A mutex flag — only one flow (print, live view, share-pull, queue-transfer) may own the notify channel at a time. |
