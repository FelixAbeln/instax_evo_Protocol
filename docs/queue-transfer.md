# Queue-button transfer — camera-initiated download

← [Wiki index](README.md)

When the user presses the **QUE button on the camera body**, the camera enters
"download queue" mode: every print that is sitting in its internal queue
(photos shot on the camera but not yet pulled by the phone) becomes available
for transfer over BLE. The app then walks the queue and pulls each JPEG.

This is **different** from the [phone-initiated pull](image-pull.md)
(`(0x85,xx)` + `(0x88,xx)`) which is triggered when the user taps "Share" on
the camera for a single image: QUE-button transfer is a bulk pull of the
entire stored print queue using the `(0x84,xx)` + `(0x80,15)` + `(0x82,xx)`
sequence.

> Confirmed via live capture 2026-05-17 (Wide Evo FI028) and used by
> `instax_lab.evo_protocol.InstaxCamera.download_history_image()` /
> the `evo-lab history pull` CLI command.

Raw QUE transfer flow excerpts are tracked in
[queue-transfer-evidence.md](queue-transfer-evidence.md).

## High-level flow

```
1. Detect readiness          ──► CAMERA_FUNCTION_INFO[4] == 0x01  (or QUE pressed)
2. Query queue length        ──► (0x84,0x09)  idx=0       → count at bytes[10:14]
3. For each index 0..N-1:
     a. Per-entry handshake  ──► (0x84,0x09) (0x84,0x0a) (0x84,0x0b)
     b. Prepare image stream ──► (0x80,0x15)  17×0x00
     c. Open stream          ──► (0x82,0x00)  [index]
     d. Pull until EOI       ──► (0x82,0x01) … loop
     e. Close stream         ──► (0x82,0x02)  [0x00]
4. (optional) decrement local queue counter; UI refresh
```

## Detecting readiness

The phone has two signals it can poll to know that the camera's queue is
available for transfer:

| Signal | How to read | Meaning when set |
|---|---|---|
| `CAMERA_FUNCTION_INFO[4]` (= response byte 4 of `(0x00,0x02,[0x04])`) | Status poll | `0x01` → camera is in transfer-ready mode. Raised by the camera ~700 ms after the user presses QUE *or* the phone sends `(0x85,0x01)` for a single image. |
| `CAMERA_FUNCTION_INFO[5]` (FI019 observed) | Status poll | On Mini Evo probes, behaves like a count-like byte that increments with additional Share-queued images. Treat as heuristic only; `(0x84,0x09)` remains authoritative for exact count. |
| `(0x85,0x00)` transfer state | Send packet, parse 5 B reply | 5-byte state vector. Reply seen `00 00 ff 00 00` when no transfer is in progress; values change once QUE is pressed (see [image-pull.md](image-pull.md)). |
| `(0x84,0x09)` with `idx=0` | One-shot query | `bytes[10:14]` (4 B BE) = number of entries currently in the queue. `0` if empty. |

Most reliable: poll `CAMERA_FUNCTION_INFO[4]` at ~1 s cadence (the same
poll loop already used for Share-button pulls). When it flips to `0x01`,
issue `(0x84,0x09,[0x00])`; if `count > 0` start the per-entry pull loop.

## Per-entry sequence

Repeat once per index `i` in `0 … count − 1`:

```
phone → cam: op=(0x84,0x09)  payload=[i]              # entry query
cam → phone: op=(0x84,0x09)  14 B (FI028, all zeros)
                          OR 1 B  (FI019: 0x80)       — value is unused

phone → cam: op=(0x84,0x0a)  payload=[i, 00,00,00,00] # entry subquery
phone → cam: op=(0x84,0x0b)  payload=[i]              # entry ACK

phone → cam: op=(0x80,0x15)  payload=[00 × 17]        # download prepare
phone → cam: op=(0x82,0x00)  payload=[i]              # download start
cam → phone: op=(0x82,0x00)  ACK

# ── pull loop ────────────────────────────────────────────────────────────
loop:
  phone → cam: op=(0x82,0x01)  payload=[]             # empty pull request
  cam → phone: op=(0x82,0x01)  payload=[chunk_idx:2B][JPEG bytes…]
               followed by 0..N raw ATT notifications carrying continuation
               of the same JPEG (no Link framing, MTU-sized fragments)
  if buffer contains b"\xff\xd9" (JPEG EOI):
      truncate at EOI+2 and exit loop
  if no data for 2 s OR (0x82,0x02) received:
      exit loop
# safety cap: 1000 pulls max per image

phone → cam: op=(0x82,0x02)  payload=[0x00]           # close stream
cam → phone: op=(0x82,0x02)  ACK
```

## Timing & fine-tuning

Empirically validated against Wide Evo FI028 (MTU=247, single bonded session):

| Parameter | Value | Notes |
|---|---|---|
| Pull cadence | **As fast as the camera responds** — no inter-pull sleep | Each `(0x82,0x01)` is sent immediately after the previous burst drains. |
| First-notification timeout | **5.0 s** | Time to wait for the *first* fragment of a pull. Camera typically responds in ~200 ms; the long timeout absorbs the occasional 1–3 s stall. |
| Continuation-fragment timeout | **0.5 s** | Once a burst starts, fragments arrive back-to-back. 0.5 s of silence ⇒ this pull's burst is complete. |
| End-of-image detection | First `b"\xff\xd9"` in accumulated buffer | Camera does *not* always emit a `(0x82,0x02)` end-frame — relying on EOI is more reliable. |
| End-of-transfer fallback | `(0x82,0x01)` payload ≤ 2 bytes **or** explicit `(0x82,0x02)` | Treat either as "no more data". |
| Safety cap | **1000 pulls / image** | A typical Wide image (~220 KB) needs ~176 pulls; the cap exists only to break runaway loops. |
| Post-frame drain | ~50 ms | Catch a spontaneous `(0x82,0x02)` arriving just after the last JPEG fragment. |

The pull responses arrive as a **mix of framed `(0x82,0x01)` notifications and
raw ATT notifications** (continuation fragments of the same JPEG with no Link
framing). The reassembler must accept both and concatenate `payload[2:]` for
framed responses and the entire `data` for raw ones, then search for EOI in
the running buffer.

## Per-model behaviour

| | Wide Evo (FI028) | Mini Evo (FI019) |
|---|---|---|
| Step 3a response size | 14 B (zeros) | 1 B (`0x80`) |
| Step 3d single image | ~176 pulls × ~1.2 KB ≈ 220 KB JPEG | similar |
| `(0x84,0x09)` with `idx=0` | Returns 14 B with count at `[10:14]` | Same |
| Notes | Works reliably after bonded reconnect | Confirmed 2026-05-17 |

## Cross-references

- Phone-initiated single-image pull (Share button): [image-pull.md](image-pull.md) — uses `(0x85,xx)` + `(0x88,xx)`.
- `(0x84,xx)` HIST tally protocol (separate use of the same opcode family for the *Usage History* counters): [history-log.md](history-log.md).
- Session-init prerequisites: [session-init.md](session-init.md). In particular `(0x80,0x10)` must have been sent during init or the camera will not emit fresh entries while BLE is connected.
- Live view (which reuses `(0x82,0x00/0x01/0x02)` in a different mode): [live-view.md](live-view.md).
