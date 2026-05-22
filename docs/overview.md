# Overview

← [Wiki index](README.md)

For raw capture excerpts behind topic claims, use [evidence.md](evidence.md).

## Protocol coverage status (as of 2026-05-21)

Status legend: `✅` confirmed working, `⚠️` partial/conditional, `⏳` not yet validated, `❌` currently failing.

| Feature | Evo Wide FI028 (Gen 2) | Mini Evo FI019 (Gen 1) | Mini Evo Cinema Gen 3 | Opcode(s) |
|---|---|---|---|---|
| BLE connect / handshake | ✅ | ✅ | ⏳ Pending first hardware capture | `(00,00)` + `(00,01)` |
| Status poll (battery, photos left, model) | ✅ | ✅ | ⏳ Pending first hardware capture | `(00,02)` |
| Transfer-ready flag detection | ✅ | ⚠️ flag seen, transfer path still not usable | ⏳ Pending first hardware capture | `(00,02)` `CAMERA_FUNCTION_INFO` byte[2] |
| **Print** (phone → camera → film ejected) | ✅ | ✅ | ⏳ Pending first hardware capture | `(80,xx)` print opcodes |
| Flash control | ✅ Confirmed `reg 0x0B` mapping and working app flow (`0=Auto, 1=On, 2=Off`) | ❌ Direct FI019 probes and repo-app writes still unresponsive as of 2026-05-21 | ⏳ Pending first hardware capture | `(80,11)` reg_id=0x0b |
| Live view (pull loop) | ✅ | ✅ Works with warm-up: early pulls may return short `0x02` payloads before JPEG frames begin | ⏳ Pending first hardware capture | `(82,00/01/02)` |
| Auto-transfer / Download Photo `(82,10/20/21/22)` | ✅ seamless LV resume after shutter close | ⚠️ works only with app-style live-view stop; standalone `(82,10)` during active live view still returns `c0` | ⏳ Pending first hardware capture | `(82,10/20/21/22)` |
| Share-button image pull | ✅ | ❌ Camera disconnects on `(88,00)` | ⏳ Pending first hardware capture | `(88,00…0b)` |
| History log / shot & print counts | ✅ HIST buffer **fully mapped** — 37×44 diagonal-banded tally; all 10 films × 10 lens positions confirmed live 2026-05-19 | ⏳ Not tested | ⏳ Pending first hardware capture | `(84,xx)` `(00,02)` InfoType 5 |
| Live shot counter (lifetime) | ✅ `CAMERA_HISTORY_INFO` payload[5] increments per shot | ✅ Confirmed incrementing in live probe (`84 -> 102` over 120 s with shots) | ⏳ Pending first hardware capture | `(00,02)` InfoType 5 |
| Print/transfer counters | ✅ `PRINT_HISTORY_INFO` readable | ⚠️ Fields readable (`transfers=60`, `prints=16`); increment trigger still under test | ⏳ Pending first hardware capture | `(00,02)` InfoType 3 |
| Camera settings registers (0x0B–0x1B) | ✅ Startup sweep mapped further: `0x0B` flash, `0x0C` film style, `0x13` film effect, `0x14` lens effect, `0x16` exposure candidate | ❌ Direct reads/writes still not confirmed on FI019 | ⏳ Pending first hardware capture | `(80,11)` read/write |
| `DEVICE_INFO` strings 0x03/0x04/0x05 | ✅ Firmware version strings (main / sub / BLE) | ❓ Unknown | ⏳ Pending first hardware capture | `(00,01)` InfoType 3–5 |
| Secondary GATT service (`0x6387…`) | ❓ Unknown | ❓ Unknown | ❓ Unknown | possibly OTA / config |

### Gen 3 bring-up tracker (for Gen 4 handoff)

Track Gen 3 in this same matrix as soon as hardware is available, then extract
the repeated bring-up sequence for Gen 4:
- Phase 1: link/connect + status reads (`00,00/01/02`) and model identity.
- Phase 2: print baseline (`80,xx`) and settings register behavior (`80,11`).
- Phase 3: live-view + receive path (`82,00/01/02/10/20/21/22`) including warm-up behavior.
- Phase 4: share/history path (`84,xx`, `88,xx`) and queue semantics (`00,02` sub=0x04 / `84,09`).
- Phase 5: promote stable findings into reusable onboarding notes for the next generation.

### Gen 1 vs Gen 2 vs Gen 3 (current project state)

| Capability | Gen 1 FI019 | Gen 2 FI028 | Gen 3 Mini Evo Cinema |
|---|---|---|---|
| Print `(10,xx)` | ✅ Working | ✅ Working | ⏳ Pending first hardware capture |
| Status/model/battery/photos `(00,01/02)` | ✅ Working | ✅ Working | ⏳ Pending first hardware capture |
| `InfoType 0x03` counters | ⚠️ Readable; increment semantics still being validated | ✅ Readable | ⏳ Pending first hardware capture |
| `InfoType 0x05` shot counter | ✅ Readable (probe-confirmed) | ✅ Used live | ⏳ Pending first hardware capture |
| Live view `(82,00/01/02)` | ✅ Working, warm-up tolerant | ✅ Stable | ⏳ Pending first hardware capture |
| `82` picture receive flow `(82,10/20/21/22)` | ⚠️ Works after live-view stop; direct `(82,10)` during active live view still fails with `c0` | ✅ Working | ⏳ Pending first hardware capture |
| Share-button pull `(88,xx)` | ❌ Disconnects | ✅ Working | ⏳ Pending first hardware capture |

The practical split is now clear: FI028 has the full remote-shoot control path
working in the repo app, while FI019 currently has working print/live view/`0x82`
download but still lacks a known-good direct flash-write path and still rejects
the `0x88` share-pull family.

## Two BLE profiles, two protocols (historical)

Every Instax camera with BLE advertises **two separate BLE profiles** simultaneously:

| Profile | BLE address prefix | Protocol | Used by |
|---|---|---|---|
| **Link** (a.k.a. `(IOS)` / `(BLE)`) | `FA:AB:BC:xx:xx:xx` | Link protocol (`41 62` / `61 42` framing) | Instax iOS app, javl/InstaxBLE, **this project** |
| **Android** | `E0:48:24:xx:xx:xx` (Mini Evo) | Legacy binary (`16xx`/`17xx` writes) | Instax Android app only |

Both profiles share the **same GATT service and characteristic UUIDs** but speak
entirely different application protocols. This project targets the **Link
profile** exclusively; the Android profile is documented for completeness only
in [android-legacy.md](android-legacy.md).

> **Note:** `javl/InstaxBLE` filters by `INSTAX-` prefix + `(IOS)` suffix.
> The Wide Evo advertises as `INSTAX-<serial>(BLE)`; despite the different
> suffix it still speaks the Link protocol. Filter on protocol behaviour
> (presence of the `70954782-…` service UUID), not on advertising name.

## Confirmed camera models

| Model | Model ID | Gen | Link-profile address | Film | Smartphone print | Shots remaining |
|---|---|---|---|---|---|---|
| Instax Mini Evo | **FI019** | 1 | `FA:AB:BC:11:6F:D2` | instax mini | **600 × 800** (portrait) | 1 ✓ live |
| Instax Evo Wide | **FI028** | 2 | `FA:AB:BC:1D:0A:7B` | instax Wide | **1260 × 840** (landscape) | 4 ✓ HCI log |
| Instax Mini Evo Cinema | *(unknown)* | 3 | *(not captured)* | instax mini | **800 × 600** (landscape cinema) | — |

Notes:
- Gen 1 BR/EDR address `88:B4:36:11:6F:D2` is a Fujifilm-OUI classic Bluetooth address — **not BLE**.
- Model IDs from `DEVICE_INFO_SERVICE` op=(0x00,0x01) InfoType=1: FI019 (Mini Evo), FI028 (Evo Wide).
- BLE device name suffix = serial number: `INSTAX-3332137670 (IOS)` → serial `3332137670`.
- Gen 1 may require a one-time Windows passkey/PIN pairing step after firmware or bond resets; current maintained app flow does not treat `client.pair()` as a required per-session step.
- Gen 3 (Mini Evo Cinema) is not in our possession; assumed to use the same Link protocol.

## Shared GATT service (all models, both profiles)

| UUID | Role |
|---|---|
| `70954782-2d83-473d-9e5f-81e1d02d5273` | Instax primary service |
| `70954783-2d83-473d-9e5f-81e1d02d5273` | **Write characteristic** (Write + WriteNoResp) |
| `70954784-2d83-473d-9e5f-81e1d02d5273` | **Notify characteristic** (subscribe for responses) |

These UUIDs are shared across all known models and both profiles.

## GATT handle layout (Link profile)

### Gen 1 — Mini Evo (`FA:AB:BC:11:6F:D2`)

Recovered from live probe session:

| Handle | Props | UUID | Role |
|---|---|---|---|
| h=0x0014 | Write, WriteNoResp | `70954783-...` | Write char |
| h=0x0016 | Notify | `70954784-...` | Notify char |
| h=0x0018 | — | `0x2902` CCCD | Write `01 00` to enable notifications |

### Gen 2 — Evo Wide (`FA:AB:BC:1D:0A:7B`)

Full GATT table from 19-51-52 HCI capture:

| Handle range | Service UUID | Purpose |
|---|---|---|
| 0x0001–0x0004 | `0x1801` Generic Attribute | Service Changed (h=0x0003, CCCD h=0x0004) |
| 0x0005–0x000D | `0x1800` Generic Access | Device name, appearance, etc. |
| **0x000E–0x0013** | `70954782-2d83-473d-9e5f-81e1d02d5273` | **Instax primary service** |
| 0x0014–0x0026 | `0x180A` Device Information | DIS — manufacturer, model, serial, FW |
| 0x0027–0x003B | `0000d0ff-3c17-d293-8e48-14fe2e4da212` | Fujifilm secondary service |
| 0x003C–0xFFFF | `00006287-3c17-d293-8e48-14fe2e4da212` | Fujifilm tertiary service |

Instax primary service characteristics:

| Handle | Props | UUID | Role |
|---|---|---|---|
| **h=0x0010** | Write, WriteNoResp | `70954783-...` | **Write char** |
| **h=0x0012** | Notify | `70954784-...` | **Notify char** |
| **h=0x0013** | — | `0x2902` CCCD | Write `01 00` to enable notifications |

Device Information service:

| Handle | UUID | Returns |
|---|---|---|
| h=0x0016 | `0x2A29` Manufacturer Name | `"FUJIFILM"` |
| h=0x0018 | `0x2A24` Model Number | `"FI028"` |
| h=0x001A | `0x2A25` Serial Number | `"92007814"` |
| h=0x001C | `0x2A27` Hardware Revision | (unknown) |
| h=0x001E | `0x2A26` Firmware Revision | (unknown) |
| h=0x0020 | `0x2A28` Software Revision | (unknown) |
| h=0x0022 | `0x2A23` System ID | (unknown) |
| h=0x0024 | `0x2A2A` Regulatory | (unknown) |
| h=0x0026 | `0x2A50` PnP ID | (unknown) |

Fujifilm secondary service chars (h=0x0027–0x003B):

| Handle | Props | UUID | Purpose |
|---|---|---|---|
| h=0x0029 | WriteNoResp | `0xFFD1` | Unknown (possibly OTA write) |
| h=0x002B | Read | `0xFFD2` | Unknown |
| h=0x002D | Read | `0xFFD3` | Unknown |
| h=0x002F | Read | `0xFFD4` | Unknown |
| h=0x0031 | Read | `0xFFF1` | Unknown |
| h=0x0033 | Read | `0xFFE0` | Unknown |
| h=0x0035 | Read | `0xFFE1` | Unknown |
| h=0x0037 | Read | `0xFFF3` | Unknown |
| h=0x0039 | Read | `0xFFF4` | Unknown |
| h=0x003B | Read | `0xFFF5` | Unknown |

Fujifilm tertiary service chars (h=0x003C–0xFFFF):

| Handle | Props | UUID | Purpose |
|---|---|---|---|
| h=0x003E | WriteNoResp | `00006387-3c17-d293-8e48-14fe2e4da212` | Unknown write |
| h=0x0040 | Write, Notify | `00006487-3c17-d293-8e48-14fe2e4da212` | Unknown cmd+notify |
| h=0x0041 | — | `0x2902` CCCD | CCCD for h=0x0040 |

The Fujifilm secondary/tertiary services are not used by the Link protocol.
They may carry OTA firmware updates or factory config; out of scope here.
