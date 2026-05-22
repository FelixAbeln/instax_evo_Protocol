# Instax Evo BLE Protocol — Wiki

Reverse-engineered notes for the Instax camera/printer BLE protocol used by the
Mini Evo, Evo Wide, and (assumed) Mini Evo Cinema.

This wiki is split into topical pages. The pages in this index are the current
source of truth.

## Terminology

Throughout this wiki we call the active protocol the **Link protocol** — the
same protocol used by Instax Link printers and the Instax iOS app. The legacy
docs called this the "IOS profile" because the camera advertises it under names
ending in `(IOS)`/`(BLE)`; the protocol itself has nothing to do with iOS.

The separate "Android profile" used by the Instax Android app is described once
in [android-legacy.md](android-legacy.md) — we don't use it in this project.

## If you're implementing the protocol from scratch

Start here — this is the minimum required to ship a working client:

1. **[quickstart.md](quickstart.md)** — single-page runnable walkthrough:
   connect, init, print, live view, share-pull, auto-transfer. All Python
   skeletons inlined; no other reading required to get a basic client working.
2. **[link-protocol.md](link-protocol.md)** — the canonical opcode table and
   packet framing reference.
3. **[glossary.md](glossary.md)** — terminology and notation used everywhere
   else (InfoType, slot, HIST, transfer-ready flag, etc.).

After those three, jump to the per-flow page you need from the table below.

## Full reading order (topical)

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
11. [favorites.md](favorites.md) — confirmed FI028 favorites read/write path and remaining field-semantic gaps
12. [favorites-evidence.md](favorites-evidence.md) — raw favorites flows and capture excerpts
13. [effects-by-model.md](effects-by-model.md) — model-scoped Film/Lens/Style name catalogs
14. [model-quirks.md](model-quirks.md) — Gen 1 / Gen 2 / Gen 3 differences
15. [implementation.md](implementation.md) — Windows/bleak quirks, capture logs, local print log
16. [evidence.md](evidence.md) — cross-topic evidence index and capture logging pattern
17. [roadmap.md](roadmap.md) — open hypotheses, known gaps, references
18. [todo.md](todo.md) — active near-term task list and exit criteria
19. [glossary.md](glossary.md) — terms, abbreviations, notation
20. [android-legacy.md](android-legacy.md) — Android profile (not used)

## Cross-cutting topics

- All pages share the same opcode names defined in [link-protocol.md](link-protocol.md).
- All wire examples assume the Link protocol unless stated otherwise.
- "Gen 1" = Mini Evo (FI019), "Gen 2" = Evo Wide (FI028), "Gen 3" = Mini Evo Cinema.

## Evidence pages

Raw captures and timestamped wire excerpts are split into paired evidence pages
so explanation pages stay concise. Start from [evidence.md](evidence.md).

- [session-init-evidence.md](session-init-evidence.md)
- [print-evidence.md](print-evidence.md)
- [live-view-evidence.md](live-view-evidence.md)
- [auto-transfer-evidence.md](auto-transfer-evidence.md)
- [image-pull-evidence.md](image-pull-evidence.md)
- [queue-transfer-evidence.md](queue-transfer-evidence.md)
- [history-log-evidence.md](history-log-evidence.md)
- [registers-evidence.md](registers-evidence.md)
- [favorites-evidence.md](favorites-evidence.md)
- [model-quirks-evidence.md](model-quirks-evidence.md)
- [implementation-evidence.md](implementation-evidence.md)

## Confirmed cameras

| Gen | Model | Model ID | BLE address (Link profile) |
|-----|-------|----------|----------------------------|
| 1   | Instax Mini Evo        | `FI019` | `FA:AB:BC:11:6F:D2` |
| 2   | Instax Evo Wide        | `FI028` | `FA:AB:BC:1D:0A:7B` |
| 3   | Instax Mini Evo Cinema | *(unknown)* | *(not in possession)* |
