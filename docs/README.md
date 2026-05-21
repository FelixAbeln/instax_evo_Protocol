# Instax Evo BLE Protocol — Wiki

Reverse-engineered notes for the Instax camera/printer BLE protocol used by the
Mini Evo, Evo Wide, and (assumed) Mini Evo Cinema.

This wiki is split into topical pages. The pages in this index are the current
source of truth. The legacy file [protocol-legacy.md](protocol-legacy.md) is
retained only as a historical archive and still contains superseded hypotheses,
stale terminology, and exploratory notes that have since been corrected here.

## Terminology

Throughout this wiki we call the active protocol the **Link protocol** — the
same protocol used by Instax Link printers and the Instax iOS app. The legacy
docs called this the "IOS profile" because the camera advertises it under names
ending in `(IOS)`/`(BLE)`; the protocol itself has nothing to do with iOS.

The separate "Android profile" used by the Instax Android app is described once
in [android-legacy.md](android-legacy.md) — we don't use it in this project.

## Reading order

1. [overview.md](overview.md) — coverage matrix, supported cameras, GATT service
2. [link-protocol.md](link-protocol.md) — packet framing, opcode table, InfoType values
3. [session-init.md](session-init.md) — connection / pairing / required init sequence
4. [print.md](print.md) — end-to-end print pipeline + image preparation
5. [live-view.md](live-view.md) — `(0x82,xx)` viewfinder stream
6. [auto-transfer.md](auto-transfer.md) — `(0x82,10/20/21/22)` post-shutter image pull
7. [image-pull.md](image-pull.md) — `(0x85,xx)` + `(0x88,xx)` on-demand share-button pull
8. [queue-transfer.md](queue-transfer.md) — QUE-button bulk queue download (`(0x84,xx)` + `(0x80,15)` + `(0x82,xx)`)
9. [history-log.md](history-log.md) — `(0x84,xx)` HIST: shot/print log + 37×44 histogram
10. [registers.md](registers.md) — `(0x80,11)` register table (flash, lens effect, etc.)
11. [model-quirks.md](model-quirks.md) — Gen 1 / Gen 2 / Gen 3 differences
12. [implementation.md](implementation.md) — Windows/bleak quirks, capture logs, local print log
13. [roadmap.md](roadmap.md) — open hypotheses, known gaps, references
14. [android-legacy.md](android-legacy.md) — Android profile (not used)

## Cross-cutting topics

- All pages share the same opcode names defined in [link-protocol.md](link-protocol.md).
- All wire examples assume the Link protocol unless stated otherwise.
- "Gen 1" = Mini Evo (FI019), "Gen 2" = Evo Wide (FI028), "Gen 3" = Mini Evo Cinema.

## Confirmed cameras

| Gen | Model | Model ID | BLE address (Link profile) |
|-----|-------|----------|----------------------------|
| 1   | Instax Mini Evo        | `FI019` | `FA:AB:BC:11:6F:D2` |
| 2   | Instax Evo Wide        | `FI028` | `FA:AB:BC:1D:0A:7B` |
| 3   | Instax Mini Evo Cinema | *(unknown)* | *(not in possession)* |
