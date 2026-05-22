# Queue State Machine Notes (2026-05-22)

Source: live FI028 run in `instax_lab.gui` with enhanced queue diagnostics.

## Key console sequence

- `INFO04 change: profile=0b50 ready=0x02 q_like=5 phase=12/2`
- first `(88,xx)` pull completes
- `INFO04 change: profile=0b50 ready=0x01 q_like=5 phase=13/1`
- second `(88,xx)` pull completes
- `INFO04 change: profile=0b50 ready=0x00 q_like=5 phase=14/0`

In the same run, `(84,09)` repeatedly reported `idx00=0, idx02=1` and later
returned short payloads (`02`) for both indices after drain completion.

## Conclusion (provisional)

For FI028 Share flow with `sub=0x04` profile `0b50`:

- `b13` (phase second byte) acts like remaining entries (`2 -> 1 -> 0`).
- `ready` byte moves with transfer phase (`0x00 -> 0x02/0x01 -> 0x00`).
- `(84,09)` is not a reliable remaining-depth source during this flow.

## Implementation impact

- Queue UI should favor decoded `info04_remaining` when profile semantics are
  known (currently includes `0b50`, `0232`, `0350`, `035f`).
- Keep `(84,09)` for supplemental diagnostics and fallback only.
