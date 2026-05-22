# Registers — `(0x80,0x11)` SET_INFO

← [Wiki index](README.md)

The Evo Link profile exposes a per-feature register interface via the
`SET_INFO` opcode `(0x80,0x11)`. On FI028 this is used heavily at session start
to mirror the remote-shooting UI and to apply flash changes. FI019 still has no
reliable direct `(0x80,0x11)` ACKs in our live probes, so treat the mapping
below as FI028-confirmed unless noted otherwise.

Raw register sweep and read/write excerpts are tracked in
[registers-evidence.md](registers-evidence.md).

## Register access format

```
READ  phone→cam: (0x80,0x11)  payload=[reg_id][0x00×5]
      cam→phone: (0x80,0x11)  payload=[0x00][reg_id][current_value][0x00×3]

WRITE phone→cam: (0x80,0x11)  payload=[reg_id][0x02][new_value][0x00×3]
      cam→phone: (0x80,0x11)  payload=[0x00][reg_id][0x00×4]   (ACK — does not echo new value)
```

## Flash mode — `reg_id=0x0B` (confirmed)

| `new_value` | Flash setting |
|---|---|
| `0x00` | AUTO |
| `0x01` | ON (forced flash) |
| `0x02` | OFF (no flash) |

**Startup read:** Phone sends `[0x0b 0x00 0x00 0x00 0x00 0x00]`; camera replies
with `[0x00 0x0b <current> 0x00 0x00 0x00]`.

**Example writes from the 2026-05-21 FI028 three-photo remote-shooting session:**

```
Flash OFF:  phone→cam (0x80,0x11) payload=0b 02 02 00 00 00
Flash ON:   phone→cam (0x80,0x11) payload=0b 02 01 00 00 00
Flash AUTO: phone→cam (0x80,0x11) payload=0b 02 00 00 00 00
```

The three photos in that session were captured as `ON`, then `OFF`, then
`AUTO`, and the only per-shot setting delta was the value written to `reg 0x0B`
immediately before the `(0x82,10)` transfer sequence. Live view stayed up.

## Register table — `(0x80,0x11)` READ response

Response payload: `[reg_id: 2B BE][value: 1B][param: 1B][00 00]`

```
READ  phone→cam: (0x80,0x11)  payload=[reg_id][0x00×5]   (6 bytes)
      cam→phone: (0x80,0x11)  payload=[0x00][reg_id][value][param][0x00][0x00]   (6 bytes)
                                                      ^^^^^  ^^^^^
                                                      byte2  byte3
```

| reg_id | name | value byte (byte[2]) | param byte (byte[3]) | Fresh FI028 remote-shooting read | Confidence |
|--------|-----------------|----------------------|----------------------|----------------|-----------|
| 0x0B | Flash mode | 0x00–0x02 | 0x00 | `000b 01 00 0000` | **Confirmed.** `0=AUTO`, `1=ON`, `2=OFF` |
| 0x0C | Film Style | 0x00 | 0x00 | `000c 00 00 0000` | Strong candidate: `0=OFF` |
| 0x13 | Film Effect | 0x01–0x0A | 0x00 | `0013 02 00 0000` | Strong candidate from screenshot-aligned session: `2=Vivid` |
| 0x14 | Lens Effect | 0x01–0x0A | 0x00 | `0014 01 00 0000` | Strong candidate from screenshot-aligned session: `1=Normal` |
| 0x15 | unknown | 0x00 | 0x00 | `0015 00 00 0000` | Unresolved |
| 0x16 | Exposure comp | 0x00 | session-dependent | `0016 00 2f 0000` | Strong candidate: value byte is exposure offset; param is not fixed |
| 0x17 | Film Effect tally register | 0x01–0x0A | 0x00 | `0017 01 00 0000` | Confirmed useful for live HIST/tally attribution; no longer treated as the only active UI film selector |
| 0x18 | unknown | 0x00 | 0x00 | `0018 00 00 0000` | — |
| 0x19 | unknown | 0x00 | 0x00 | `0019 00 00 0000` | — |
| 0x1A | unknown | 0x00 | 0x00 | `001a 00 00 0000` | — |
| 0x1B | Lens Effect tally register | 0x01–0x0A | 0x00 | `001b 01 00 0000` | Confirmed useful for live HIST/tally attribution; no longer treated as the only active UI lens selector |

## Film/Lens value enums used by tally-facing registers

The value space is still useful even though the active remote-shooting screen
appears to read `0x13/0x14` for film/lens selection. `0x17/0x1B` remain useful
for live HIST/tally attribution, and the same 1-based enum names apply.

| Value | `reg 0x17` Film mode | | Value | `reg 0x1b` Lens effect |
|-------|----------------------|-|-------|------------------------|
| 1 | Normal      | | 1 | Normal |
| 2 | Vivid       | | 2 | Light Leak |
| 3 | Warm        | | 3 | Light Prism |
| 4 | Sky Blue    | | 4 | Vignette |
| 5 | Light Green | | 5 | Soft Glow |
| 6 | Magenta     | | 6 | Double Ex. |
| 7 | Sepia       | | 7 | Color Shift |
| 8 | Monochrome  | | 8 | Monochrome Blur |
| 9 | Amber       | | 9 | Color Gradient |
| 10 | Summer     | | 10 | Beam Flare |

## Reading registers at runtime

`(0x80,0x11)` reads are **non-destructive** and may be called as often as
needed. For live tally attribution, the recommended pattern is to read
`reg 0x17` and `reg 0x1b` once per shot — i.e. each time `CAMERA_HISTORY_INFO` (see
[history-log.md § Runtime polling](history-log.md#runtime-polling-for-live-counters))
shows the lifetime shot counter has incremented — so each new shot can be
attributed to the active Film+Lens combination at the moment of capture.

```python
async def read_reg(send, recv, reg_id: int) -> int | None:
    await send(make_packet(0x80, 0x11, bytes([reg_id, 0, 0, 0, 0, 0])))
    _, _, p = await recv(timeout=2.0)
    return p[2] if len(p) >= 3 else None

film = await read_reg(send, recv, 0x17)   # 1..10
lens = await read_reg(send, recv, 0x1b)   # 1..10
```

> **Current reading:** the fresh 2026-05-21 FI028 remote-shooting capture shows
> the app reading `(0x80,0x11)` at connect time and displaying values that line
> up with `0x0B` flash, `0x0C` film style, `0x13` film effect, `0x14` lens
> effect, and `0x16` exposure. White balance and some remaining registers are
> still unresolved, but the older blanket claim that these registers were not a
> source of visible UI state is no longer correct.
