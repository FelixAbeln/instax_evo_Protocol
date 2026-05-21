# FI019 validation notes

Target camera: Instax Mini Evo (FI019)

Address used in the probe sessions: `FA:AB:BC:11:6F:D2`

This file is kept as working validation notes rather than wiki documentation.
The probe campaign is no longer being tracked as an open checklist; the repo
now keeps only the smaller set of maintained scripts that still map to
regression checks or open questions.

## Retained probe scripts

- `scripts/fi019_test_status.py`
- `scripts/fi019_test_counters.py`
- `scripts/fi019_test_liveview.py`
- `scripts/fi019_test_image_transfer.py`
- `scripts/fi019_test_flash.py`
- `scripts/fi019_test_flash_liveview.py`
- `scripts/fi019_link_reg_probe.py`
- `scripts/test_download_photo_button_flow.py`

## Finalized findings

- Status reads work.
  - Manufacturer/model/serial/image size/battery/photos-left are readable.
- `InfoType 0x03` is readable.
  - `transfers` and `prints` fields decode cleanly.
  - Increment behavior is still less important than the field decode itself and
    is not blocking app support.
- `InfoType 0x05` works.
  - Lifetime shot counter increments live during capture.
- Live view works.
  - Early `(0x82,0x01)` pulls can return short warm-up payloads before JPEG
    frames start.
- `0x82` image receive works.
  - The working Gen 1 path is the app-style sequence around live view state.
  - Directly treating `(0x82,0x10)` as a generic shutter opcode was the wrong
    mental model.
- `0x88` share-pull does not work on FI019 in this repo.
  - `(0x88,0x00)` still causes disconnect.
- Direct Link-profile flash writes remain unresolved.
  - `(0x80,0x11)` on `reg 0x0B` is confirmed on FI028 but still does not give a
    reliable direct ACK on FI019 in the repo app.

## Historical note

The earlier raw/live-view experiment scripts were useful during reverse
engineering but were intentionally removed once the stable model-specific
conclusions above were documented. Their conclusions survive in the docs; the
one-off runners do not.