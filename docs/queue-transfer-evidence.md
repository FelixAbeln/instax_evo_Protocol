# Queue transfer evidence

← [Wiki index](README.md)

Raw QUE-button transfer traces and queue-length probe windows for
[queue-transfer.md](queue-transfer.md).

## Capture table

| ID | File | Notes |
|---|---|---|
| QUE-01 | captures/new_log_0517b/* | Queue readiness and per-index transfer windows |
| QUE-02 | captures/analysis/queue_state_machine_2026-05-22.md | FI028 live Share run: `sub=0x04` profile `0b50` phase countdown `2 -> 1 -> 0` matched two pulls while `(84,09)` stayed `idx02=1` |

## Raw flow excerpts

### QUE-02 (FI028 live run 2026-05-22)

- Pre-transfer steady state:
	- `INFO04 change: profile=0b50 ready=0x00 q_like=5 phase=0/0`
	- `Queue status: hist_probe=1 ... idx00:0 idx02:1`
- Transfer start:
	- `INFO04 change: profile=0b50 ready=0x02 q_like=5 phase=12/2`
	- first pull completes (`[1 image(s) pulled]`)
- Mid-drain:
	- `INFO04 change: profile=0b50 ready=0x01 q_like=5 phase=13/1`
	- second pull completes (`[2 image(s) pulled]`)
- Drain complete:
	- `INFO04 change: profile=0b50 ready=0x00 q_like=5 phase=14/0`
	- `(84,09)` may return short `02` responses, then still reports stale
		`idx02=1` in subsequent probes.

Interpretation: for profile `0b50`, `sub=0x04` byte 13 (`b13`) behaves like a
remaining-entry countdown in Share flow. `(84,09)` is a secondary signal and is
not reliable as remaining-depth during this path.
