# Registers — `(0x80,0x11)` SET_INFO

← [Wiki index](README.md)

The Wide Evo (FI028) exposes a per-feature register interface via the
`SET_INFO` opcode `(0x80,0x11)`. The phone reads the current settings at
startup and writes them back when the user changes the corresponding toggle in
the app.

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

**Example writes from bugreport 0517b (Wide Evo):**

```
Flash OFF:  phone→cam (0x80,0x11) payload=0b 02 02 00 00 00
Flash ON:   phone→cam (0x80,0x11) payload=0b 02 01 00 00 00
Flash AUTO: phone→cam (0x80,0x11) payload=0b 02 00 00 00 00
```

All three flash changes happened during an ongoing live view session — the BLE
connection stays up and the live view session does not need to be interrupted
to change flash.

## Register table — `(0x80,0x11)` READ response

Response payload: `[reg_id: 2B BE][value: 1B][param: 1B][00 00]`

```
READ  phone→cam: (0x80,0x11)  payload=[reg_id][0x00×5]   (6 bytes)
      cam→phone: (0x80,0x11)  payload=[0x00][reg_id][value][param][0x00][0x00]   (6 bytes)
                                                      ^^^^^  ^^^^^
                                                      byte2  byte3
```

| reg_id | name (suspected) | value byte (byte[2]) | param byte (byte[3]) | Bugreport 0518 | Confidence |
|--------|-----------------|----------------------|----------------------|----------------|-----------|
| 0x0B | Flash / WB | 0x02 | 0x00 | `000b 02 00 0000` | **Flash write confirmed** (0=AUTO, 1=ON, 2=OFF); read value=2 suspected WB |
| 0x0C | Film Style | 0x00 | 0x00 | `000c 00 00 0000` | Suspected (0=OFF) |
| 0x13 | unknown | 0x00 | 0x00 | `0013 00 00 0000` | — |
| 0x14 | unknown | 0x00 | 0x00 | `0014 00 00 0000` | — |
| 0x15 | unknown | 0x00 | 0x00 | `0015 00 00 0000` | — |
| 0x16 | Exposure comp | 0x00 | 0x32=50 | `0016 00 32 0000` | Suspected: value=0=center; param=50=±range |
| 0x17 | Film Effect | 0x01–0x0A | 0x00 | `0017 01 00 0000` | **Confirmed.** 1=Normal…10=Summer; see enum below. |
| 0x18 | unknown | 0x00 | 0x00 | `0018 00 00 0000` | — |
| 0x19 | unknown | 0x00 | 0x00 | `0019 00 00 0000` | — |
| 0x1A | unknown | 0x00 | 0x00 | `001a 00 00 0000` | — |
| 0x1B | Lens Effect | 0x01–0x0A | 0x00 | `001b 01 00 0000` | **Confirmed.** 1=Normal…10=Beam Flare; see enum below. |

## Film Effect (`reg 0x17`) and Lens Effect (`reg 0x1b`) enums

Both registers hold a 1-based enum value 1–10. The same 10 lens names are
available across all 10 film modes (lens #1 is always "Normal"). Confirmed
from the app's "Usage History" screen and from live HIST scans on 2026-05-19.

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
needed. The recommended pattern is to read `reg 0x17` and `reg 0x1b` once per
shot — i.e. each time `CAMERA_HISTORY_INFO` (see
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

> **Note:** These registers reflect the camera's **current settings** at
> connect time — they are NOT the source of the per-image detail screen in the
> app. The image detail screen (Film, Lens, Exposure, WB, Film Style) is
> populated from **locally saved metadata** stored by the app when each image
> was pulled via `(0x88,01)` IMAGE_TRANSFER_INFO; no live BLE message is sent
> when viewing image detail. More captures with varied settings are needed to
> confirm the exact semantic mapping of each register index to its setting
> name.
