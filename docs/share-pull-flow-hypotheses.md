# Share Pull Flow And Mutation Hypotheses

<- [Wiki index](README.md)

This page is a compact mutation map for the share-pull path.

Primary goal:
- isolate the first packet or state gate that causes FI019 disconnects
- keep experiments small and reproducible

Companion script:
- [scripts/share_pull_mutation_probe.py](../scripts/share_pull_mutation_probe.py)

## Reference flow (known-good on FI028)

1. Optional transfer-mode prep: `(0x85,00) -> (0x85,01) -> (0x85,00)`
2. Wait until support-info sub `0x04` ready flag is non-zero
3. Start transfer: `(0x88,00)`
4. Metadata: `(0x88,01)` payload `00000000`
5. Chunk requests: `(0x88,02)` for chunk indices
6. Close: `(0x88,03)` then `(0x88,05)`

## Why FI019 may fail

1. Feature mismatch:
   FI019 may expose share-ready state but still not implement the `0x88` family.
2. State mismatch:
   FI019 may require a hidden precondition before accepting `0x88,00`.
3. Metadata mismatch:
   FI019 images may not carry the same compact metadata assumptions as FI028,
   so `0x88,01` may be invalid or unnecessary.
4. Strict close/ordering rules:
   FI019 may reject start if previous transfer state was not closed exactly.

## Mutation matrix

Run each case twice to reduce one-off BLE timing effects.

1. Case A baseline:
   prep 85 yes, wait flag yes, 88,01 yes, chunks 1, close yes.
   Expected FI028: ack/start path works.
   Expected FI019: likely disconnect near `0x88,00`.

2. Case B no metadata:
   same as A but skip `0x88,01`.
   If FI019 survives longer, metadata path is a likely incompatibility.

3. Case C start-only:
   same as A but chunks 0.
   If FI019 disconnects before chunking, failure is in start gate not payload.

4. Case D no 85 prep:
   skip 85 prep, keep wait flag and 88 flow.
   If behavior changes, `0x85` prep is part of required state on that model.

5. Case E delayed start:
   add post-flag delay (for example 0.7s then 2.0s).
   If one delay works better, there is likely a timing gate after UI transition.

6. Case F shifted chunk index:
   start chunk at 1 with chunks 1.
   If index 0 fails but index 1 behaves differently, index semantics differ.

## Suggested command lines

```powershell
# A) Baseline
python scripts/share_pull_mutation_probe.py --prep-85 --chunks 1 --tag A_baseline

# B) Skip metadata
python scripts/share_pull_mutation_probe.py --prep-85 --skip-88-01 --chunks 2 --tag B_no_meta

# C) Start only (no chunk requests)
python scripts/share_pull_mutation_probe.py --prep-85 --chunks 0 --tag C_start_only

# D) No 85 prep
python scripts/share_pull_mutation_probe.py --chunks 1 --tag D_no_85

# E) Post-flag delay variation
python scripts/share_pull_mutation_probe.py --prep-85 --post-flag-delay 2.0 --chunks 1 --tag E_delay2s
```

## What to capture per run

1. Did connection survive after `0x88,00`?
2. First unexpected frame opcode and payload length
3. Whether `0x88,01` returned short, error-like, or full metadata
4. Whether any JPEG SOI marker appears in chunk data
5. Exact step where disconnect occurred

The script writes a trace file for each run in captures/image_transfer.
