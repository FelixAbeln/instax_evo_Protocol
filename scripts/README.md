# Scripts

This folder now keeps the smaller set of scripts that are still useful as
repeatable probes or trace-analysis helpers.

## Maintained FI019 probes

- `fi019_test_status.py` — baseline status and support-info reads
- `fi019_test_counters.py` — watch `InfoType 0x03` / `0x05` counters live
- `fi019_test_liveview.py` — validate `(0x82,00/01/02)` live view
- `fi019_test_image_transfer.py` — check the unsupported `(0x88,xx)` share path
- `fi019_test_flash.py` — direct `(0x80,0x11)` flash/register probe
- `fi019_test_flash_liveview.py` — flash/register probe while live view is open
- `fi019_link_reg_probe.py` — conservative Link-profile register read/write probe
- `test_download_photo_button_flow.py` — app-style `0x82` download flow reproducer

## Other helpers

The retained non-FI019 helpers are the small set still useful for recurring log
comparison work:

- `analyze_bugreport_trace.py`
- `compare_trace_files.py`

## Cleanup note

The older one-off raw/live-view experiment runners were removed after the Gen 1
and Gen 2 behavior split was documented. If a future investigation needs that
kind of probing again, add a new focused script with a narrow documented goal
instead of restoring the whole exploratory set.