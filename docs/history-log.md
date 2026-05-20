# History log — `(0x84,xx)` HIST + 37×44 histogram

← [Wiki index](README.md)

The `(0x84,xx)` HIST sequence is sent by the app on **every BLE connect**. It
serves two purposes:

1. **Detect pending downloads** — slot 02 count > 0 → prompt user to pull images.
2. **Retrieve usage history** — slot 00 and slot 02 contain per-shot and
   per-print event records that the app counts to populate the "Usage History"
   screen (Shots, Prints, Film Effect tallies).

Confirmed from btsnoop 2026-05-18 (Wide Evo, official Instax app) and live
systematic scan 2026-05-19.

> **Not what you're looking for?** If you want the **camera-initiated QUE-button
> bulk image download** — where the user presses QUE on the camera body and the
> phone pulls every queued JPEG — that flow uses `(0x84,xx)` only as a length
> probe and then switches to `(0x80,0x15)` + `(0x82,xx)`. See
> [queue-transfer.md](queue-transfer.md).
>

## Full HIST opcode table

| op1 | op2 | Name | Direction | Notes |
|-----|-----|------|-----------|-------|
| 0x84 | 0x00 | `HIST_INFO` | P→C empty; C→P 13B | Session info |
| 0x84 | 0x01 | `HIST_INIT` | P→C 4B `[00×4]`; C→P 9B `[00×9]` | Initialise session |
| 0x84 | 0x02 | `HIST_START` | P→C empty; C→P 1B `[00]` | Commit / start |
| 0x84 | 0x09 | `HIST_LIST_REQ` | P→C 1B `[slot]`; C→P 14B | Query slot metadata |
| 0x84 | 0x0a | `HIST_GET_DATA` | P→C 5B `[slot:2B][00×3]`; C→P variable | Retrieve slot data |
| 0x84 | 0x0b | `HIST_DONE` | P→C 1B `[slot]`; C→P 2B `[slot:2B]` | Finalise slot read |

## Wire sequence

```
phone → cam: op=(0x84,0x00)  payload=[]
cam → phone: op=(0x84,0x00)  13B  [00000000 04000000 04000000 00]   # HIST_INFO

phone → cam: op=(0x84,0x01)  payload=[0x00 0x00 0x00 0x00]
cam → phone: op=(0x84,0x01)  9B   [0x00 × 9]                        # HIST_INIT

phone → cam: op=(0x84,0x02)  payload=[]
cam → phone: op=(0x84,0x02)  1B   [0x00]                            # HIST_START

# ── for each slot to read (app reads slot 0x00 then slot 0x02) ──────────────
phone → cam: op=(0x84,0x09)  payload=[slot]
cam → phone: op=(0x84,0x09)  14B  [slot:2B][record_size:4B BE][record_size:4B BE][count:4B BE]

  # if count == 0: skip to HIST_DONE
  # if count > 0:
phone → cam: op=(0x84,0x0a)  payload=[slot:2B][0x00×3]
cam → phone: op=(0x84,0x0a)  (6 + record_size) bytes                # HIST_GET_DATA

phone → cam: op=(0x84,0x0b)  payload=[slot]
cam → phone: op=(0x84,0x0b)  2B   [slot:2B]                         # HIST_DONE
```

## Critical behaviour notes

- The camera **writes new shot records in real-time** even while BLE is
  connected, **but only if the session sent `(0x80,10)` during init** (see
  [session-init.md](session-init.md)). Scripts that omit `(0x80,10)` will
  never see `count=1` for connected shots.
- **Timing:** The camera writes the HIST record **~3–4 seconds after the
  physical shutter fires**. `HIST_LIST_REQ (0x84,09)` polled too quickly
  (<3 s after the shot) returns `count=0`. Wait at least 3 s after detecting a
  shot via `CAMERA_HISTORY_INFO` before querying `(0x84,09)`.
- There is **no confirmed dedicated "HIST ready" flag** analogous to the image
  transfer ready bit. The practical readiness probe is `HIST_LIST_REQ` on slot
  `0x00`: while the async write is still pending, `count=0`; once ready,
  `count>0` and `HIST_GET_DATA` returns the new window.
- **`HIST_DONE (0x84,0b)` clears the buffer.** After sending it, the camera
  resets all tally counts to zero. The next `HIST_GET_DATA (0x84,0a)` only
  contains shots accumulated *since the last `(0x84,0b)`*.
- During a live session the iOS app may also skip `HIST_GET_DATA` (issue
  `HIST_DONE` only). Per-effect counting is then done in real-time via
  `CAMERA_HISTORY_INFO` (sub=05 shot counter) + reg `0x17` (Film Effect) /
  `0x1b` (Lens Effect) at each counter increment.

## `HIST_LIST_REQ (0x84,09)` — slot metadata

14-byte response: `[slot:2B][record_size:4B BE][record_size:4B BE][count:4B BE]`

| Slot | Content | Bugreport 0518 example |
|------|---------|------------------------|
| `0x00` | **Shot event log** | `0000 00000664 00000664 00000001` → record_size=1636, count=1 |
| `0x02` | **Print event log** | `0002 0000066e 0000066e 00000001` → record_size=1646, count=1 |

- `count=0`: slot is empty (no new events since last read).
- `count=1`: one composite record is ready.

## `HIST_GET_DATA (0x84,0a)` — slot payload format

The camera returns `6 + record_size` bytes:

```
[6B zeros (header/status)][record_data: record_size bytes]
```

`record_data` layout:

```
[date_str: 8B ASCII "YYYYMMDD"][event_records: N × record_size_each bytes]
```

| Slot | Record data size | Event record size | Derived count |
|------|------------------|-------------------|---------------|
| `0x00` | 1636 B | **44 bytes** | (1636−8)/44 = **37** |
| `0x02` | 1646 B | **234 bytes** | (1646−8)/234 = **7** |

## Shot slot — 37×44-byte histogram

> **Important:** Slot 0's 1628-byte body is a **fixed-size 2D tally buffer**,
> NOT a list of 37 individual shot records. The camera always returns exactly
> 1636 bytes for slot 0; the "37 records" count is the arithmetic artefact
> 1628 ÷ 44 = 37 and carries no meaning by itself.

The 37×44-byte body is indexed as:

```
rec[film_reg − 1][lens_reg]   ← shot count for that Film+Lens combination
```

where `film_reg` is the value returned by register `0x17` and `lens_reg` is the
byte position (always an **odd** number) within the 44-byte record.

**Global counters (written for every shot regardless of Film/Lens setting):**

| Cell | Meaning |
|------|---------|
| `rec[0][1]`   | **Global total shots counter** |
| `rec[36][21]` | **Global total shots mirror** — identical to `rec[0][1]` |

**Decoding a live shot:** The camera increments three cells per shot:
1. `rec[0][1]` — global total
2. `rec[film_reg − 1][lens_reg]` — per-Film+Lens tally
3. `rec[36][21]` — global total mirror

On current Film=1 firmware, `rec[0][1]` should be treated as the global total
only. The Film=1 per-lens bytes are shifted and do not directly use byte 1.

## Film mode and lens effect names

The 10 lens effect names are **identical across all 10 film modes** — lens
effect #1 is always "Normal" regardless of which film mode is active.

| Film # | Film mode name (`reg 0x17`) | | Lens # | Lens effect name (`reg 0x1b`) |
|--------|----------------------------|-|--------|-------------------------------|
| 1 | Normal | | 1 | Normal |
| 2 | Vivid | | 2 | Light Leak |
| 3 | Warm | | 3 | Light Prism |
| 4 | Sky Blue | | 4 | Vignette |
| 5 | Light Green | | 5 | Soft Glow |
| 6 | Magenta | | 6 | Double Ex. |
| 7 | Sepia | | 7 | Color Shift |
| 8 | Monochrome | | 8 | Monochrome Blur |
| 9 | Amber | | 9 | Color Gradient |
| 10 | Summer | | 10 | Beam Flare |

> **Note on Double Ex. (lens #6):** In Film=3 (Warm), the Double Ex. slot at
> `rec[2][7]` never increments from a single shutter press — the effect
> requires two sequential app presses to fire. In all other film modes the
> #6 slot was confirmed to increment normally.

## Per-film `lens_reg` byte mapping

All 10 film modes were systematically scanned live on 2026-05-19. Each table
records the byte offset within the indicated `rec` row for every lens slot.

### Film=1 (Normal)

Confirmed by systematic live scan and re-validated in Instax Lab runtime logs.
Film=1 has **two byte skips**: byte 3 is the Double Ex. slot and byte 15 is
an unknown skip unique to Film=1.

| App lens # | Effect | byte in `rec[0]` |
|---|---|---|
| #01 | Normal | **5** |
| #02 | Light Leak | **7** |
| #03 | Light Prism | **9** |
| #04 | Vignette | **11** |
| #05 | Soft Glow | **13** |
| #06 | Double Ex. | **3** |
| #07 | Color Shift | **17** (skip byte 15) |
| #08 | Monochrome Blur | **19** |
| #09 | Color Gradient | **21** |
| #10 | Beam Flare | **23** |

Sequence: `5 · 7 · 9 · 11 · 13 · [DE at 3] · [skip 15] · 17 · 19 · 21 · 23`.

### Film=2 (Vivid)

Clean consecutive odd-byte sequence 1–19 within `rec[1]` with **no skips**.

| Lens # | byte |
|---|---|
| #01–#10 | 1 · 3 · 5 · 7 · 9 · 11 · 13 · 15 · 17 · 19 |

### Film=3 (Warm)

Spans `rec[1]` (bytes 41, 43) and `rec[2]` (bytes 1–15). The DE slot at
`rec[2][7]` is special: it requires two sequential app presses to complete
one cycle, and increments **once per completed cycle** (first press leaves no
HIST trace).

| Lens # | rec · byte |
|---|---|
| #01 Normal | r1·b41 |
| #02 Light Leak | r1·b43 |
| #03 Light Prism | r2·b1 |
| #04 Vignette | r2·b3 |
| #05 Soft Glow | r2·b5 |
| #06 Double Ex. | r2·b7 † (2-press cycle) |
| #07 Color Shift | r2·b9 |
| #08 Monochrome Blur | r2·b11 |
| #09 Color Gradient | r2·b13 |
| #10 Beam Flare | r2·b15 |

### Films 4–10 (uniform pattern, no skips)

All seven follow the regular diagonal pattern: 10 consecutive lens slots
starting at the row/byte shown below, with no DE quirks.

| Film | Start | Lens #01 … #10 byte sequence |
|---|---|---|
| **F4 Sky Blue** | r2·b37 | r2·b37, b39, b41, b43 → r3·b1, b3, b5, b7, b9, b11 |
| **F5 Light Green** | r3·b33 | r3·b33, b35, b37, b39, b41, b43 → r4·b1, b3, b5, b7 |
| **F6 Magenta** | r4·b29 | r4·b29, b31, b33, b35, b37, b39, b41, b43 → r5·b1, b3 |
| **F7 Sepia** | r5·b25 | r5·b25, b27, b29, b31, b33, b35, b37, b39, b41, b43 |
| **F8 Monochrome** | r6·b21 | r6·b21, b23, b25, b27, b29, b31, b33, b35, b37, b39 |
| **F9 Amber** | r7·b17 | r7·b17, b19, b21, b23, b25, b27, b29, b31, b33, b35 |
| **F10 Summer** | r8·b13 | r8·b13, b15, b17, b19, b21, b23, b25, b27, b29, b31 |

## Structural pattern — diagonal banding

The HIST buffer encodes film positions using a diagonal banding structure.
Each odd byte in the 37×44 grid has a flat index:

```
flat_idx = rec_num × 22 + (byte − 1) // 2
```

Only odd bytes carry data (lens_reg values are always odd).

**Film mode start positions:**

| Film | Start flat | rec[R] start byte | Formula |
|---|---|---|---|
| Film=1 | 0 | rec[0][1] | special (see deviations) |
| Film=2 | 22 | rec[1][1] | — |
| Film=3 | 42 | rec[1][41] | 22 + 1×20 |
| Film=4 | 62 | rec[2][37] | 22 + 2×20 |
| Film=5 | 82 | rec[3][33] | 22 + 3×20 |
| Film=6 | 102 | rec[4][29] | 22 + 4×20 |
| Film=7 | 122 | rec[5][25] | 22 + 5×20 |
| Film=8 | 142 | rec[6][21] | 22 + 6×20 |
| Film=9 | 162 | rec[7][17] | 22 + 7×20 |
| Film=10 | 182 | rec[8][13] | 22 + 8×20 |

For Film=N (N ≥ 2): **start_flat = 20N − 18**, **start_byte = 53 − 4N**
within `rec[N−2]`.

Each film mode occupies **10 consecutive flat positions** (10 lens slots); the
10 positions following each block (flat +10 through +19) are unused. Total
stride = 20 flat positions.

If a future firmware exposes Film=11+, the predicted addresses are
`rec[9][9]` (flat 202) and `rec[10][5]` (flat 222).

## Complete 10×10 position grid

Each cell shows `rR·bB` = record row R, byte B within that row.
† = Double Exposure slot (two-press cycle).

| Film | #01 | #02 | #03 | #04 | #05 | #06 | #07 | #08 | #09 | #10 |
|---|---|---|---|---|---|---|---|---|---|---|
| **F1** | r0·b1 | r0·b5 | r0·b7 | r0·b9 | r0·b11 | r0·b13 | r0·b17 | r0·b19 | r0·b21 | r0·b23 |
| **F2** | r1·b1 | r1·b3 | r1·b5 | r1·b7 | r1·b9 | r1·b11 | r1·b13 | r1·b15 | r1·b17 | r1·b19 |
| **F3** | r1·b41 | r1·b43 | r2·b1 | r2·b3 | r2·b5 | r2·b7† | r2·b9 | r2·b11 | r2·b13 | r2·b15 |
| **F4** | r2·b37 | r2·b39 | r2·b41 | r2·b43 | r3·b1 | r3·b3 | r3·b5 | r3·b7 | r3·b9 | r3·b11 |
| **F5** | r3·b33 | r3·b35 | r3·b37 | r3·b39 | r3·b41 | r3·b43 | r4·b1 | r4·b3 | r4·b5 | r4·b7 |
| **F6** | r4·b29 | r4·b31 | r4·b33 | r4·b35 | r4·b37 | r4·b39 | r4·b41 | r4·b43 | r5·b1 | r5·b3 |
| **F7** | r5·b25 | r5·b27 | r5·b29 | r5·b31 | r5·b33 | r5·b35 | r5·b37 | r5·b39 | r5·b41 | r5·b43 |
| **F8** | r6·b21 | r6·b23 | r6·b25 | r6·b27 | r6·b29 | r6·b31 | r6·b33 | r6·b35 | r6·b37 | r6·b39 |
| **F9** | r7·b17 | r7·b19 | r7·b21 | r7·b23 | r7·b25 | r7·b27 | r7·b29 | r7·b31 | r7·b33 | r7·b35 |
| **F10** | r8·b13 | r8·b15 | r8·b17 | r8·b19 | r8·b21 | r8·b23 | r8·b25 | r8·b27 | r8·b29 | r8·b31 |

The diagonal staircase is clear: each row shifts 4 bytes left within the
record and 2 bytes further into the next record.

## Deviations — Film=1 and Film=3

The **normal pattern** (Films 2, 4–10): lens #K occupies byte `start_byte +
2(K−1)` within its leading record, wrapping to the next record after 22
positions. No gaps between consecutive lens slots.

**Film=1 — two byte skips:**

If Film=1 followed the normal rule it would mirror Film=2's layout in rec[0].
Instead two bytes are absent:

- **Byte 3** — DE slot. Same two-press mechanic as Film=3's `rec[2][7]`: only
  the second press writes to HIST.
- **Byte 15** — cause unconfirmed; possibly a second two-press effect,
  firmware padding, or a legacy artefact unique to Film=1.

The skips shift the low Film=1 lenses away from the naive byte-1 start:

- lenses #01–#05 land on bytes `5,7,9,11,13`
- lens #06 (Double Ex.) uses byte `3`
- lenses #07–#10 land on bytes `17,19,21,23`

**Film=3 — one byte skip (DE only):**

Film=3 follows the normal pattern exactly except `rec[2][7]` is the DE slot
and fires only on completed two-press cycles. Subsequent lens positions are
**not** shifted — the firmware allocates the slot whether it fires or not.

## Python decoder

```python
FILM_NAMES = {
    1: "Normal", 2: "Vivid", 3: "Warm", 4: "Sky Blue", 5: "Light Green",
    6: "Magenta", 7: "Sepia", 8: "Monochrome", 9: "Amber", 10: "Summer",
}
LENS_NAMES = {
    1: "Normal", 2: "Light Leak", 3: "Light Prism", 4: "Vignette", 5: "Soft Glow",
    6: "Double Ex.", 7: "Color Shift", 8: "Monochrome Blur", 9: "Color Gradient",
    10: "Beam Flare",
}

def hist_shot_count(hist_body: bytes, film: int, lens: int) -> int:
    """Return the per-film+lens shot counter.

    hist_body is the 1628-byte payload (record_data minus the 8B date prefix).
    Indexed as rec[film-1][lens_byte] where lens_byte is the odd byte position
    within the 44-byte record. The per-film byte mapping varies (see the
    legacy file for full tables); a simple uniform encoding is:
    """
    flat = (film - 1) * 10 + (lens - 1)
    rec_num = flat // 22
    byte_in_rec = (flat % 22) * 2 + 1
    return hist_body[rec_num * 44 + byte_in_rec]

def hist_film_total(hist_body: bytes, film: int) -> int:
    return sum(hist_shot_count(hist_body, film, k) for k in range(1, 11))

def hist_lens_total(hist_body: bytes, lens: int) -> int:
    return sum(hist_shot_count(hist_body, n, lens) for n in range(1, 11))

total_shots = hist_body[1]   # rec[0][1]; mirrors hist_body[36*44 + 21]
```

## Print slot (slot 0x02) — 234-byte record

From bugreport 0518 (7 prints, all with Normal settings):

```
rec[0]: 00 00 00 01 00 00 00 01 00 01 00 01 00 01 00 01 00 00…
rec[1]…rec[6]: [00 × 234]   (all zeros)
```

Non-zero bytes in `rec[0]` (first print of this sync period): byte 3, 7, 9, 11,
13, 15 = `0x01`. These six fields likely represent per-print setting counts or
indices (film effect, lens effect, exposure, WB, film style, etc.) — exact
mapping TBD. All remaining print records are all-zeros, consistent with
default/Normal settings.

## `HIST_INFO (0x84,00)` response — session-level counters

```
00 00 00 00  04 00 00 00  04 00 00 00  00   (13 bytes, bugreport 0518)
             ^^^^^^^^^^^  ^^^^^^^^^^^
             uint32 LE=4  uint32 LE=4   ← semantics TBD; NOT the queue depth
```

These fields are NOT the shot/print counts — use `HIST_LIST_REQ` slot counts.

## Keepalive poll cycle (Evo Wide)

The app continuously polls `SUPPORT_FUNCTION_INFO` InfoTypes in a round-robin
while idle:

```
→ 0x00,0x02 InfoType=0x04  (CAMERA_FUNCTION_INFO)  ← cam: 02 32 00×14 [18B]
→ 0x00,0x02 InfoType=0x05  (CAMERA_HISTORY_INFO)   ← cam: 00 00 17    [6B]
→ 0x00,0x02 InfoType=0x02  (PRINTER_FUNCTION_INFO) ← cam: 26 00 00 0c 00×4 [10B]
→ 0x00,0x02 InfoType=0x03  (PRINT_HISTORY_INFO)    ← cam: 00 00 00 04 00 00 00 05 [10B]
→ 0x00,0x02 InfoType=0x01  (BATTERY_INFO)          ← cam: 02 32 00 00 [6B]
Repeat every ~0.5 s
```

`CAMERA_HISTORY_INFO byte[2]` is the **live shot counter** — increments by 1
each time a photo is taken whether BLE-connected or not. With `(0x80,10)` sent
during session init, shots taken while BLE-connected ARE written to the HIST
buffer in real-time. When the counter increments, the app reads `reg 0x17`
(Film Effect) and `reg 0x1b` (Lens Effect) to attribute the shot to the
correct per-effect category.

## Runtime polling for live counters

There are **two independent counters** you can read at any time after session
init. They have different semantics — pick the right one for the job.

| Counter | Opcode | Where in response | Behaviour |
|---|---|---|---|
| **Lifetime shot counter** | `(0x00,0x02,[0x05])` `CAMERA_HISTORY_INFO` | payload byte `[5]` (= response byte `[8+? = 5]` of the 6 B payload — last byte) | **Non-destructive.** Increments for every shutter press, whether BLE was connected or not. Polling is cheap; safe to call every poll cycle. |
| **HIST per-effect tallies** | `(0x84,0x00/01/02/09/0a/0b)` full HIST sequence above | 37×44 histogram body | **Destructive on `(0x84,0x0b)`.** Returns shots accumulated since the previous `(0x84,0x0b)`. After the ACK the camera resets all tallies to zero. Counts shots taken while disconnected; with `(0x80,0x10)` in session init it also counts shots taken while connected. |

**Recommended polling pattern** (also implemented by `map_hist.py`):

```python
# Once per session, during init (see docs/session-init.md):
await write(make_packet(0x00, 0x00))                 # hello
# ... DEVICE_INFO + SUPPORT_FUNCTION_INFO reads ...
await write(make_packet(0x20, 0x10))                 # FW_PROGRAM_INFO
await write(make_packet(0x80, 0x10, b"\x00"))        # enable live HIST writing

# Then, every ~1 s while idle:
await write(make_packet(0x00, 0x02, b"\x05"))        # CAMERA_HISTORY_INFO
_, _, pay = await recv()
counter = pay[5]                                     # lifetime counter
if counter != prev_counter:
    # Optionally read which film+lens were active for the just-fired shot:
    await write(make_packet(0x80, 0x11, bytes([0x17,0,0,0,0,0])))
    _, _, r = await recv();  film_reg = r[2]
    await write(make_packet(0x80, 0x11, bytes([0x1b,0,0,0,0,0])))
    _, _, r = await recv();  lens_reg = r[2]
    log_shot(counter, film_reg, lens_reg)
    prev_counter = counter
```

Refreshing the full `(0x84,xx)` HIST is **only** needed when you actually want
the per-film+lens histogram (e.g. to populate the "Usage History" screen) —
remember that doing so resets the camera-side tallies. For a simple "image
total that updates while connected", `CAMERA_HISTORY_INFO[5]` is sufficient
and non-destructive.

> **HIST counters are session subtotals, not lifetime accumulators.**
>
> The camera zeroes the entire HIST buffer every time the phone sends
> `(0x84,0x0b) HIST_DONE` — which the official Instax app does on **every**
> BLE connect, immediately after reading the buffer. There is **no
> camera-side cumulative per-Film/per-Lens total** that survives reads.
>
> Consequence: the app must **maintain its own running totals on the phone
> side**. The accepted pattern is:
>
> 1. On connect: read the full HIST buffer once via the `(0x84,…)`
>    sequence, add each `rec[film−1][lens]` cell into a local database,
>    then send `(0x84,0x0b)` to clear the camera buffer.
> 2. While connected: poll `CAMERA_HISTORY_INFO[5]` every ~1 s; on each
>    increment, read `reg 0x17` (Film) and `reg 0x1b` (Lens) and
>    increment the local `totals[film][lens]` cell by 1.
> 3. The "image total" shown in the UI is the **sum of the local totals
>    table**, never a value read directly from the camera.
>
> See [protocol-legacy.md §HIST summary](protocol-legacy.md) for the
> original wire-trace evidence (2026-05-19, shot #151).

The app's `BleWorker._poll_loop` ([instax_lab/gui.py](../instax_lab/gui.py))
uses exactly this pattern: it polls `(0x00,0x02,[0x04])` for the
transfer-ready flag and `(0x00,0x02,[0x05])` for the shot counter on every
cycle, and emits a `shot_counter` UI event whenever the value changes.

## Evaluate mode (HIST matrix-first attribution)

Validated from Instax Lab runtime logs for Film=1 on 2026-05-20:

- `L1 -> rec[0][5]`
- `L2 -> rec[0][7]`
- `L3 -> rec[0][9]`
- `L4 -> rec[0][11]`
- `L5 -> rec[0][13]`
- `L6 -> rec[0][3]` (Double Ex.)
- `L7..L10 -> rec[0][17,19,21,23]`
- `rec[0][1]` remains the global total only

For app-side attribution, decode Film=1 directly from that shifted byte map
instead of inferring a Film=1 lens bucket from the global total cell.

Practical Film=1 interpretation:

- `rec[0][1]` is the slot-global total for the current HIST window.
- the first five Film=1 lens buckets are shifted right by one logical index
  because byte `3` is reserved for Double Ex.
- byte `15` remains unused/unknown and causes the later +4 shift.

Recommended live timing:

- Detect shot by `CAMERA_HISTORY_INFO (sub=0x05)` increment.
- Wait ~3 seconds (camera HIST write latency).
- Read slot 0 via `(0x84,00/01/02/09/0a/0b)` and apply evaluation above.

Avoid using immediate post-shot `reg 0x17/0x1b` as the sole source of truth for
count attribution; those reads can be transient during mode transitions.

Runtime validation logs (Instax Lab)

The app logs the decoded window summary for each seed/harvest so you can verify
the algorithm directly against captured HIST data:

```text
EVALUATE seed: G=<global> known=<k> unknown=<u>
EVALUATE harvest: G=<global> known_add=<k> added=<a>
EVALUATE cells: F1/L1=<n>, F1/L2=<n>, ...
```

Where:

- `G` = `rec[0][1]` (global total for this window)
- `known` / `known_add` = sum of directly mapped film/lens cells in the window
- `unknown` = absolute mismatch between global total and decoded mapped cells
- `added` = total counts merged from this harvested window into app totals
- `EVALUATE cells` = decoded non-zero `(film,lens)` buckets for that window
