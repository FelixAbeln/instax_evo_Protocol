# Gen1 0x83 Working Notes (Scratch)

Status: active working notes for FI019 reverse-engineering.
Scope: exploratory and provisional. Not canonical docs.

## Why this file exists

- Keep fast iteration notes outside docs while the model is still uncertain.
- Track hypotheses, probe matrix, and immediate next experiments.

## Current baseline (2026-05-24)

Environment
- Camera: FI019 (Gen1), addr `FA:AB:BC:11:6F:D2`
- Tool: `scripts/share_pull_light.py`
- Gating: `--require-share-edge` (wait for post-connect info04 transition)

Observed share-edge behavior
- info04 ready stays `0x01` in window.
- `q_like` increments on share edge (for example `9 -> 10`).

Confirmed 0x83 responses after share edge
- `(83,00)` -> `[81]`
- `(83,01)` -> `[81]`
- `(83,02)` -> `[00]`
- `(83,03)` -> `[80]`
- `(83,04)` -> `[80]`
- `(83,05)` -> `[80]`

Trace reference
- `captures/image_transfer/share_light_gen1_probe_83_sweep_edge_liveness_2026-05-24_115051.trace`

## Interpretation (provisional)

- `0x83` family is live and implemented on Gen1.
- Return code split suggests opcode-level state machine:
  - likely unsupported/invalid (`0x80`/`0x81`) for some sub-ops in current state
  - likely accepted (`0x00`) at least for `op2=0x02`
- This supports "Gen1 different family" over "same 0x88 with minor extension".

## Working assumptions for next tests

- Gen1 share pull may not expose a separate metadata phase.
- `0x83,02` may be a candidate request path for data or for entering a next phase.

## Latest probes (2026-05-24, post-edge)

`0x83,02` payload mutation runs
- payload `00000000` -> response `[00]` (1 byte)
- payload `050000000000000000` -> response `[00]` (1 byte)

Sequence probes (payload `00000000`)
- `83,00` -> `[81]`
- `83,01` -> `[00 00 00 00 00]` (5 bytes)
- `83,02` -> `[00]`
- `83,03` -> `[80]`
- `83,04` -> `[80]`
- `83,05` -> `[80]`

Sequence probes (payload `00000001`)
- `83,01` -> `[00 00 00 00 01]` (5 bytes)
- `83,02` -> `[00]`
- `83,03` -> `[80]`
- `83,04` -> `[80]`
- `83,05` -> `[80]`

`83,01` behavior update
- With 4-byte payload, response length is consistently 5 bytes.
- Response body appears to be `[00][payload_u32_be]` echo for tested values 0 and 1.
- This looks like a select/index acknowledgement stage, not metadata blob.

Extended mutation matrix (`83,06..0b`)

With `83,01` pre-step and payload `00000000`:
- `83,06..0b` all return `[80]`.

With `83,01` pre-step and payload `00000001`:
- `83,06..0b` all return `[80]`.

With empty payload (no `probe_payload_hex`):
- `83,01` -> `[81]` (needs 4-byte arg to enter 5-byte echo behavior)
- `83,02` -> `[00]`
- `83,06..0b` all return `[80]`.

Implication
- No data-bearing response found in `83,06..0b` under tested contexts.
- Current strongest model remains:
   - `83,01` = index/select-like stage (4-byte arg required for full ack body)
   - `83,02` = accepted follow-up stage (status-only so far)
   - candidate data stage likely outside tested sub-op set or requires iterative state progression.

Broad op2 expansion (`83,00..1f`)

With 4-byte payload `00000000`:
- `83,00` -> `[81]`
- `83,01` -> `[00 00 00 00 00]` (5-byte echo-style body)
- `83,02` -> `[00]`
- `83,03..1f` -> `[80]` for all tested op2 values.

With 4-byte payload `00000002`:
- `83,00` -> `[81]`
- `83,01` -> `[00 00 00 00 02]` (5-byte echo-style body)
- `83,02` -> `[00]`
- `83,03..1f` -> `[80]` for all tested op2 values.

Delta interpretation
- The missing behavior is likely not in untested `op2` values `0x0c..0x1f`.
- Remaining high-probability gap is sequence/state mutation, not single-shot op2 mutation.

Staged in-session progression (`83,01(idx)` then `83,02(idx)`)

Run: idx `0..12` in one edge-gated session.

Observed pattern for every idx in range:
- `83,01(idx)` -> 5-byte echo-style body: `[00][idx_u32_be]`
- `83,02(idx)` -> `[00]` (1 byte)

No response-length escalation appeared during staged progression.

Updated implication
- The missing mutation is probably outside basic `(op2, idx_u32_be)` search space.
- Most likely remaining gaps are payload shape/encoding and prerequisite state transitions.

Payload-shape mutations (strict non-`0x88` run)

All runs used: edge gate + `--probe-only` + `op1=0x83` + `op2s=1,2`.

Little-endian-style 4-byte payload
- payload `01000000`
- `83,01` -> `[00 01 00 00 00]` (5-byte echo-style body)
- `83,02` -> `[00]`

8-byte payloads
- payload `0000000100000000` -> `83,01:[81]`, `83,02:[00]`
- payload `0000000000000001` -> `83,01:[81]`, `83,02:[00]`

12-byte and 16-byte framed payloads
- payload `000000010000000000000000` -> `83,01:[81]`, `83,02:[00]`
- payload `00000001000000000000000000000000` -> `83,01:[81]`, `83,02:[00]`

New inference from this batch
- `83,01` appears to require exactly 4 bytes for echo-style ack body.
- `83,02` still accepts broad payload shapes but remains status-only (`00`).
- No data-bearing response found; this points more strongly to missing prerequisite state transitions vs payload length alone.

Repeated `83,02` burst tests (no `0x84/0x85/0x88`)

Pattern per run:
- one `83,01(idx)`
- then `83,02(idx)` repeated 32 times in the same edge-gated session.

Results
- idx `0`:
   - `83,01` -> `[00 00 00 00 00]`
   - all 32x `83,02` -> `[00]`
- idx `1`:
   - `83,01` -> `[00 00 00 00 01]`
   - all 32x `83,02` -> `[00]`

Inference update
- No delayed unlock from repeated `83,02` requests.
- Remaining gap is likely a missing precondition/state selector outside the tested `0x83` request cadence.

Cadence mutation sweep (`83` only)

Harness update
- Added `--probe-inter-send-delay` to pace probe sends in both generic probe mode and staged mode.

Run shape
- idx `0` run pattern: one `83,01(00000000)` then `83,02(00000000)` repeated 32 times.
- Tested with inter-send delays: `0.1s`, `0.3s`, `0.7s`.

Results
- For all three pacing values:
   - `83,01` stayed 5-byte echo ack `[00 00 00 00 00]`.
   - every repeated `83,02` response remained `[00]`.
   - no response-length escalation and no alternate status code observed.

Timing inference
- No evidence that modest pacing differences unlock a data-bearing `83,02` path.
- Missing factor is likely not send cadence in this tested range.

Alternating index-window staged test (`83` only)

Harness update
- Added `--probe-83-repeat-02-per-idx` to staged mode.

Run shape
- idx `0..8`
- per idx: one `83,01(idx)` then `83,02(idx)` repeated 4 times
- inter-send delay `0.3s`

Results
- For every idx in range:
   - `83,01(idx)` -> 5-byte echo ack `[00][idx_u32_be]`
   - all 4x `83,02(idx)` -> `[00]`
- No response-length escalation and no status variation during index transitions.

Inference update
- Alternating index windows also do not unlock a data-bearing stage.
- Unknown precondition likely sits outside the tested `83,01/83,02` loop family.

Support-info delta watch during staged `0x83`

Harness update
- Added `--probe-watch-support-subs` to read selected support-info subs after each probe send.
- Fixed staged-mode integration so support-watch also runs inside `83,01/83,02` staged loops.

Verification run
- staged idx `0..2`, `83,02` repeat `2x` per idx
- watched subs: `1,2,3,4,5`

Observed
- No watched sub changed at any step after `83,01` or `83,02`.
- All remained byte-identical (`same`) through the entire staged sequence.
- `83,02` responses remained `[00]`.

Inference update
- In this state, `83` traffic does not visibly advance support-info counters/flags.
- Missing precondition likely lives outside current support-info exposure or requires a different command family we have not safely mapped yet.

Safe family scanner (no external recordings)

Harness update
- Added `--probe-op1-list` (supports ranges like `0x80-0x87`).
- Added `--probe-stop-family-on-error` for per-family early stop.

Scanner run
- op1 families: `0x80..0x87`
- op2 set: `0x00..0x03`
- support-watch subs: `1,2,3,4,5`
- stop family on first error enabled.

Observed
- `0x80`:
   - `80,00` -> `[00]`
   - `80,01` -> `[02]`
   - `80,02` timeout/error (family stopped)
- `0x81`:
   - `81,00` -> long payload (26 bytes)
   - `81,01` -> `[81]`
   - `81,02` -> `[81]`
   - `81,03` -> `[00]`
- `0x82..0x87` (starting at `op2=00`) timed out in this probe shape and were stopped per-family.
- During responsive `0x80/0x81` probes, watched support subs remained unchanged.

Actionable implication
- Best next black-box branch is deeper but safe mapping of `0x80/0x81`, not further blind `0x83` mutation.
- Candidate strategy: discover whether a specific `0x80/0x81` sequence acts as a precondition before re-running `83,01/83,02`.

Explicit pre-sequence -> `83` tests (same session)

Harness update
- Added `--probe-seq` for exact ordered `(op1,op2)` execution in one session.

Sequence A
- pre: `80,00 -> 80,01`
- then: `83,01 -> 83,02 x3`
- results:
   - `80,00` -> `[00]`
   - `80,01` -> 10-byte body `00000000000000000000`
   - `83,01` -> 5-byte echo body
   - all `83,02` -> `[00]`
   - support-watch remained stable after the pre-step value was established.

Sequence B
- pre: `81,00 -> 81,03`
- then: `83,01 -> 83,02 x3`
- results:
   - `81,00` -> 26-byte body
   - `81,03` -> `[00]`
   - `83,01` -> 5-byte echo body
   - all `83,02` -> `[00]`
   - support-watch unchanged through sequence.

Sequence C
- pre: `80,00 -> 81,00 -> 81,03`
- then: `83,01 -> 83,02 x3`
- results:
   - `80,00` -> `[00]`
   - `81,00` -> 26-byte body
   - `81,03` -> `[00]`
   - `83,01` -> 5-byte echo body
   - all `83,02` -> `[00]`
   - support-watch unchanged through sequence.

Notable signal
- `80,00` correlates with an `info04` format/state-byte shift in observed raws (`...0201xx...` -> `...0001xx...`), but this still did not unlock data-bearing `83,02` in A/C.

Inference update
- Tested `0x80/0x81` preconditioning sequences did not change `83,02` from status-only.
- Remaining unknown is likely a different command family/state axis or a stricter multi-step semantic sequence not yet represented.

Follow-up: both requested branches

1) Payload-bearing variants of sequence C (`80,00 -> 81,00 -> 81,03 -> 83,01 -> 83,02x3`)

Big-endian idx1 payload (`00000001`):
- pre-sequence remained responsive.
- `83,01` echoed idx1 as expected.
- all `83,02` remained `[00]`.
- support-watch remained stable.

Little-endian idx1 payload (`01000000`):
- pre-sequence remained responsive.
- `83,01` echoed little-endian form (`0001000000` body).
- all `83,02` remained `[00]`.
- support-watch remained stable.
- `info04` state-byte pattern observed as `...0001...` during this branch as well.

2) Deeper `0x81` branch points before `83`

Sequence: `81,00 -> 81,01 -> 81,03 -> 83,01 -> 83,02x3`
- `81,01` returned a structured 10-byte body: `000200007b0800000710`.
- `83` behavior unchanged (`83,01` echo + `83,02` status-only `[00]`).
- support-watch unchanged.

Sequence: `81,00 -> 81,02 -> 81,03 -> 83,01 -> 83,02x3`
- `81,02` returned `[81]`.
- `83` behavior unchanged (`83,01` echo + `83,02` status-only `[00]`).
- support-watch unchanged.

Updated implication
- `81,01` is payload-sensitive and information-bearing (new useful clue), but still not sufficient as a precondition for data-bearing `83,02` in tested chains.
- Next best branch is to map `81,01` payload space and chain its distinct response classes into `83` tests.

Two-option run: `81,01` payload-class mapping + immediate `83` chaining

Sequence shape for all runs
- `81,00 -> 81,01 -> 81,03 -> 83,01 -> 83,02 x3`
- support-watch on subs `1,2,3,4,5`

Observed `81,01` response classes
- payload `00000001` -> 10-byte body `000200007b0800000710`
- payload `00000002` -> same 10-byte body `000200007b0800000710`
- payload `ffffffff` -> 1-byte status `[81]`
- payload empty -> 1-byte status `[81]`

Cross-check behavior in same runs
- For payloads yielding 10-byte `81,01` body, `83,01` echoed payload and all `83,02` remained `[00]`.
- For payloads yielding `81,01:[81]`, `83,01` followed its own payload rule (4-byte -> echo body, empty -> `[81]`), and all `83,02` still remained `[00]`.

State deltas
- `81,01` with reject-class payload (`ffffffff`) changed support sub `0x01` from `...0334...` to `...0330...` in this session.
- `81,01` with empty payload stayed on the `...0330...` state during that run.
- Despite this state byte change, `83,02` stayed status-only.

Inference update
- `81,01` has at least two clear classes: accepted-structured vs reject.
- The class boundary affects support sub `0x01`, but still does not unlock data-bearing `83,02` in current chain.
- Next move: intentionally target class transitions around `81,01` (accepted -> reject -> accepted) before `83` to test for edge-triggered side effects.

Class-transition edge tests (`81,01`) before `83`

Harness capability
- `--probe-seq` now supports per-step payload overrides with `@hex` syntax, enabling mixed classes in one session.

Edge sequence 1: accepted -> reject -> accepted
- `81,00@00000000`
- `81,01@00000001` (accepted/structured)
- `81,01@ffffffff` (reject)
- `81,01@00000002` (accepted/structured)
- `81,03@00000000`
- then `83,01@00000000`, `83,02@00000000 x3`

Edge sequence 2: reject -> accepted -> reject (control)
- `81,00@00000000`
- `81,01@ffffffff` (reject)
- `81,01@00000001` (accepted/structured)
- `81,01@ffffffff` (reject)
- `81,03@00000000`
- then `83,01@00000000`, `83,02@00000000 x3`

Observed in both directions
- `81,01` class transitions occurred as expected (10-byte structured vs `81`).
- support sub `0x01` stayed in the observed `...0330...` state throughout these runs.
- `83,01` remained normal echo ack.
- all `83,02` responses remained `[00]`.

Inference update
- No edge-triggered unlock detected from `81,01` class transitions.
- `83,02` remains status-only even across deliberate class boundary crossings in the same session.

Dependency check
- Running only `83,01` then `83,02` still yields:
   - `83,01` -> `[00 00 00 00 00]`
   - `83,02` -> `[00]`
- In this observed state, `83,00` is not required before `83,01/02`.

New traces
- `captures/image_transfer/share_light_gen1_probe_8302_payload_00000000_2026-05-24_115333.trace`
- `captures/image_transfer/share_light_gen1_probe_8302_payload_9b_2026-05-24_115355.trace`
- `captures/image_transfer/share_light_gen1_probe_830102_payload4_2026-05-24_115535.trace`
- `captures/image_transfer/share_light_gen1_probe_83_full_payload4_2026-05-24_115615.trace`
- `captures/image_transfer/share_light_gen1_probe_8301_8302_only_payload4_2026-05-24_115630.trace`
- `captures/image_transfer/share_light_gen1_probe_83_seq_idx0_2026-05-24_115857.trace`
- `captures/image_transfer/share_light_gen1_probe_83_seq_idx1_2026-05-24_115921.trace`
- `captures/image_transfer/share_light_gen1_probe_83_mut_idx0_06_0b_2026-05-24_120045.trace`
- `captures/image_transfer/share_light_gen1_probe_83_mut_idx1_06_0b_2026-05-24_120101.trace`
- `captures/image_transfer/share_light_gen1_probe_83_mut_empty_1_2_06_0b_2026-05-24_120116.trace`
- `captures/image_transfer/share_light_gen1_probe_83_full_00_1f_idx0_2026-05-24_120258.trace`
- `captures/image_transfer/share_light_gen1_probe_83_full_00_1f_idx2_2026-05-24_120318.trace`
- `captures/image_transfer/share_light_gen1_probe_83_staged_0102_idx0_12_2026-05-24_120429.trace`
- `captures/image_transfer/share_light_gen1_probe_83_payload_le_idx1_rerun_2026-05-24_120625.trace`
- `captures/image_transfer/share_light_gen1_probe_83_payload_8b_headidx_2026-05-24_120642.trace`
- `captures/image_transfer/share_light_gen1_probe_83_payload_8b_tailidx_2026-05-24_120656.trace`
- `captures/image_transfer/share_light_gen1_probe_83_payload_12b_idx_pad_2026-05-24_120710.trace`
- `captures/image_transfer/share_light_gen1_probe_83_payload_16b_idx_pad_2026-05-24_120722.trace`
- `captures/image_transfer/share_light_gen1_probe_83_repeat_02_idx0_x32_2026-05-24_121023.trace`
- `captures/image_transfer/share_light_gen1_probe_83_repeat_02_idx1_x32_2026-05-24_121045.trace`
- `captures/image_transfer/share_light_gen1_probe_83_repeat_02_idx0_x32_delay100ms_2026-05-24_121145.trace`
- `captures/image_transfer/share_light_gen1_probe_83_repeat_02_idx0_x32_delay300ms_2026-05-24_121208.trace`
- `captures/image_transfer/share_light_gen1_probe_83_repeat_02_idx0_x32_delay700ms_2026-05-24_121232.trace`
- `captures/image_transfer/share_light_gen1_probe_83_staged_idx0_8_rep02x4_delay300ms_2026-05-24_121404.trace`
- `captures/image_transfer/share_light_gen1_probe_83_staged_statewatch_idx0_2_rep2_fix_2026-05-24_122055.trace`
- `captures/image_transfer/share_light_gen1_probe_safe_fams_80_87_ops_00_03_2026-05-24_122237.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_A_8000_8001_then_83_2026-05-24_122449.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_B_8100_8103_then_83_2026-05-24_122506.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_C_8000_8100_8103_then_83_2026-05-24_122524.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_C_payload_be_idx1_2026-05-24_122656.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_C_payload_le_idx1_2026-05-24_122719.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_81branch_8101_then_83_2026-05-24_122733.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_81branch_8102_then_83_2026-05-24_122746.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_8101_payload_00000001_then83_2026-05-24_124612.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_8101_payload_00000002_then83_2026-05-24_124647.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_8101_payload_ffffffff_then83_2026-05-24_124728.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_8101_payload_empty_then83_2026-05-24_124750.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_8101_edge_acc_rej_acc_then83_2026-05-24_124931.trace`
- `captures/image_transfer/share_light_gen1_probe_seq_8101_edge_rej_acc_rej_then83_2026-05-24_125005.trace`

Interim take
- `0x83,02` accepts multiple payload lengths/formats but still returns status-only.
- `0x83,01` appears parameterized (4-byte payload changed response from 1-byte status to a 5-byte body).
- No data-bearing payload observed yet.
- This suggests either:
   - `0x83,01` is a setup/select stage and `0x83,02` is an ack/request stage requiring follow-up looping or another sub-op for data, or
   - required context (mode/slot/index) is still missing.
- Need payload mutation around `0x83,02` and response-length escalation checks.

## Next probe ladder

1. Keep edge gate mandatory.
2. Probe `0x83,02` with payload variants:
   - empty
   - `00000000`
   - `00000001`
   - 9-byte pattern (`050000000000000000`)
3. After each probe, check liveness via support sub `0x04`.
4. If any response length grows beyond 1 byte:
   - preserve raw payload
   - immediately replay same request
   - try sequential 32-bit index increments (`+1`) to test chunk semantics.
5. If still 1-byte responses only:
   - test nearby sub-ops (`0x83,06..0x83,0B`) with empty + 4-byte payload.
   - test short sequence patterns (for example `83,02` then immediate `83,03/04/05`) and watch for first non-1-byte response.

## Guardrails

- Do not promote to docs until we get repeatable multi-run evidence.
- Treat all 1-byte status values as opaque until mapped by state/payload.
