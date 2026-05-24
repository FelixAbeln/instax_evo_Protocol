# Gen 1 Failure Analysis From Gen 2 Flow

← [Wiki index](README.md)

This page models why Mini Evo (FI019, Gen 1) behavior diverges by using the
fully validated Gen 2 (FI028) flow as the baseline and then applying
feature-evolution assumptions.

Use this when we cannot capture fresh FI019 traces but still need to reason
about failure points.

## Problem statement

Known today:
- Gen 2 (FI028): `(0x88,xx)` share-pull and `(0x80,0x11 reg 0x0B)` flash writes
  are stable.
- Gen 1 (FI019): `(0x88,00)` disconnects; direct flash writes often have no
  reliable ACK; `(0x82,10/20/21/22)` works only in app-style state ordering.

Goal:
- Explain failures as protocol/state evolution, not random transport noise.
- Derive practical decision rules that keep Gen 1 usable.

## Baseline: validated Gen 2 transfer/control flow

### A) Share-button pull path (Gen 2 only in current evidence)

1. poll `CAMERA_FUNCTION_INFO` (`(0x00,0x02)` sub=0x04)
2. when transfer-ready flag is set, run `(0x88,00)` start
3. request metadata `(0x88,01)`
4. pull chunked payload via `(0x88,02)` index requests
5. close with `(0x88,03)` and `(0x88,05)`

### B) Auto-transfer / Download flow (works on both gens with different trigger rules)

1. `(0x82,10)` query
2. `(0x82,20)` poll until READY (`status=0x00`, size/chunk fields)
3. `(0x82,21)` pull chunks by index
4. `(0x82,22)` close

### C) Flash write path on Gen 2

- write `(0x80,0x11)` payload `[0x0B,0x02,<mode>,0,0,0]`
- receive ACK-style `(0x80,0x11)` response

## Evolution model: what likely changed between Gen 1 and Gen 2

### E1) New transfer family was added (or hardened) in Gen 2

Most likely: `(0x88,xx)` is a later-generation feature family.

Evidence fit:
- Gen 1 still raises transfer-related status bits but hard-drops on `(0x88,00)`.
- Gen 2 fully supports `(0x88,xx)` including metadata and chunking.

Interpretation:
- Gen 1 firmware probably has no compatible `(0x88,xx)` handler, or has a strict
  gate that treats unknown/invalid transfer-start as fatal and disconnects.

### E2) State machine tightened over generations

Observed invariant on Gen 1: the `0x82` receive path is state-sensitive.

- Sending `(0x82,10)` while live view is still active can return `[0xc0]`.
- The same flow works after explicit live-view stop.

Interpretation:
- Gen 1 likely requires a strict mode transition before enabling the receive
  channel.
- Gen 2 appears to permit smoother app-managed transitions (including
  post-shutter handoff behavior).

### E3) Control register semantics evolved (ACK contract changed)

Gen 2 behavior suggests a clean write ACK contract for `reg 0x0B`.
Gen 1 appears to be weaker:
- no reliable ACK
- occasional non-ACK responses or silence

Interpretation:
- Gen 1 may apply writes asynchronously or behind a mode gate.
- ACK may be optional/unstable rather than authoritative.
- write success should be judged by readback/state effect, not ACK alone.

### E4) Session prerequisites likely expanded in later firmware

Gen 2 relies more heavily on session scaffolding (`(0x20,0x10)`, `(0x80,0x10)`,
regular info polling, queue-state cadence). Some of those steps appear optional
or less meaningful on Gen 1.

Interpretation:
- Feature bring-up was incrementally layered; older firmware can expose
  partial-compatible opcodes but not full behavior unless exact state/order is
  respected.

## Failure map by opcode family

| Family | Gen 1 failure shape | Most likely reason |
|---|---|---|
| `(0x88,xx)` | immediate disconnect on start | unsupported or hard-gated feature family |
| `(0x82,10)` during active LV | returns not-ready/error-like payload (`0xc0`) | mode arbitration requires LV stop first |
| `(0x80,0x11)` writes | missing/unstable ACK | older ACK contract or async apply path |

## Practical rules (current best strategy)

1. Never use `(0x88,xx)` on FI019 in production path.
2. For FI019 image receive, force app-style sequence:
   open LV -> pull frames -> stop LV -> run `0x82` transfer flow.
3. Treat FI019 flash set as best-effort with readback confirmation.
4. Keep model-based feature gating explicit; do not infer support from one flag.

## What this explains cleanly

- Why transfer-ready can appear but share-pull still fails on Gen 1.
- Why `0x82` receive sometimes appears broken when ordering is wrong.
- Why flash control can be real but ACK-inconsistent on older firmware.

## Open questions (still need direct FI019 capture to close)

1. Does FI019 ever ACK `0x80,0x11` writes under a specific mode/timing window?
2. Is there a Gen-1-only transfer path parallel to `(0x88,xx)` that we have not
   captured?
3. Are there additional preconditions (queue slot select, hidden register, or
   delay window) before a flash write is committed?

## Minimal validation plan once FI019 capture is possible again

1. Capture one full session around flash toggles while holding all other
   settings constant.
2. Capture one transfer-button session proving exactly which opcode family is
   used when official app downloads from FI019.
3. Diff timing and op ordering against the known FI028 baseline in
   [auto-transfer.md](auto-transfer.md), [image-pull.md](image-pull.md), and
   [registers.md](registers.md).
