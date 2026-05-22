# instax-evo-lab

Reverse-engineering the Fujifilm Instax Evo BLE protocol for print, live view,
flash control, and image download without the official app.

## Current status

Status below reflects the repo state and validated captures as of 2026-05-22.

| Model | Result |
|---|---|
| Instax Evo Wide (FI028, Gen 2) | Print, live view, flash control, remote-shoot auto-transfer, manual download, and favorites slot read/write all working |
| Instax Mini Evo (FI019, Gen 1) | Print, status, live view, and `0x82` picture download working; `0x88` share-pull disconnects; direct `0x80,0x11` flash ACKs still unresolved |

## Key points

- The project targets the Instax Link protocol carried on the `FA:AB:BC:*`
  BLE profile.
- The Android-profile traffic on `E0:48:24:*` is a different application
  protocol and is documented only for comparison.
- FI028 and FI019 share most of the control surface, but current evidence still
  supports one important model split: FI028 flash writes are confirmed, while
  FI019 still does not reliably ACK direct `0x80,0x11` flash writes in this app.
- FI028 favorites registration is now confirmed over Link BLE via
  `(0x80,0x17)` selector `0x01`/`0x02` writes bracketed by `(0x85,0x00/0x01)`.
- FI019 does support the `0x82,10/20/21/22` receive path when used with the
  app-style state sequence; the older repo claim that this path was untested on
  Gen 1 is no longer correct.

## Source of truth

The maintained protocol reference lives in the wiki-style docs index:

- [docs/README.md](docs/README.md)
- [docs/overview.md](docs/overview.md)
- [docs/session-init.md](docs/session-init.md)
- [docs/live-view.md](docs/live-view.md)
- [docs/auto-transfer.md](docs/auto-transfer.md)
- [docs/registers.md](docs/registers.md)
- [docs/model-quirks.md](docs/model-quirks.md)
- [docs/evidence.md](docs/evidence.md)

The older long-form protocol dump has been replaced with a short summary in
[docs/protocol.md](docs/protocol.md) so it no longer conflicts with the wiki.

## Setup

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Pairing notes

- FI019 generally needs to be paired in Windows first using the passkey shown on
  the camera.
- FI028 works with the current app flow without a separate manual pairing step.

## Usage

```powershell
python -m instax_lab
```

The GUI handles scanning, connecting, live view, print, remote-shoot download,
and the currently supported flash controls.

## Repo layout

- `instax_lab/` contains the maintained app and protocol implementation.
- `docs/` is the maintained protocol/wiki reference.
- `scripts/` contains focused probes and trace-analysis helpers that are still
  useful for reproducing the current findings.

## Maintained probe scripts

The repo now keeps the smaller set of repeatable probes that still map to open
questions or regression checks:

- `scripts/fi019_test_status.py`
- `scripts/fi019_test_counters.py`
- `scripts/fi019_test_liveview.py`
- `scripts/fi019_test_image_transfer.py`
- `scripts/fi019_test_flash.py`
- `scripts/fi019_test_flash_liveview.py`
- `scripts/fi019_link_reg_probe.py`
- `scripts/test_download_photo_button_flow.py`

The earlier one-off raw/live-view experiment scripts have been removed so the
repo no longer presents them as maintained workflows.

## What is working today

- BLE printing on FI019 and FI028
- Live view on FI019 and FI028
- FI028 flash control via `(0x80,0x11)` register `0x0B`
- FI028 remote-shoot auto-transfer via `(0x82,10/20/21/22)`
- FI028 favorites slot read/write via `(0x80,0x17)` + `(0x85)` save bracket
- FI019 manual/app-style image download via `(0x82,10/20/21/22)` after live
  view has been stopped

## What is still open

- FI019 direct flash writes still do not reliably ACK in the repo app
- FI019 `(0x88,xx)` share-button pull still disconnects
- Some non-flash registers in the `0x80,0x11` startup sweep remain only partly
  mapped
- FI028 favorites selector-state semantics are still partial: selector-`0x02`
  byte `4` transition behavior (`0x00/0x01/0x02/0x05`) and bit-level meaning
  are not fully mapped yet
