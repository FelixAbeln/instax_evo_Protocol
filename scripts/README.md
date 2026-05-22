# Scripts

This folder contains maintained probes and analyzers used for repeatable
protocol work.

## Queue and transfer analysis

- `analyze_phone_link_archives.py` - scans Phone Link bugreport archives
  (extracted dirs + zip files) and summarizes `(00,02)`/`(84,09)`/`(88,xx)`
  behavior per recording.
- `watch_share_flags_live.py` - live watcher for support-info changes in poll
  order `02,03,01,04,05`.
- `watch_queue_increment.py` - focused queue-like byte watcher on `sub=0x04`.
- `probe_share_no_metadata.py` - transfer probe that exercises `85/88` flow
  while allowing custom readiness gates.

## Favorites and metadata helpers

- `favorites_slot_codec.py` - decode `(80,17)` slot payloads and build write
  payloads (includes exposure encode/decode helpers).
- `favorites_live_slots.py` - dump favorites snapshots and diff camera-side
  changes.
- `decode_8801_compact.py` - decode raw `(88,01)` metadata payloads and match
  compact-tail fields against favorites snapshots.

## FI019 probes

- `fi019_test_status.py`
- `fi019_test_counters.py`
- `fi019_test_liveview.py`
- `fi019_test_image_transfer.py`
- `fi019_test_flash.py`
- `fi019_test_flash_liveview.py`
- `fi019_link_reg_probe.py`
- `test_download_photo_button_flow.py`
- `fi019_common.py` (shared LinkClient + helpers)

## HIST and trace utilities

- `hist_watch.py`
- `map_hist.py`
- `map_hist_baseline.json` (local baseline store for `map_hist.py`)
- `analyze_bugreport_trace.py`
- `compare_trace_files.py`
- `compare_flow_shapes.py`
- `sanitize_captures.py`
- `sanitize-captures.ps1`

## Archived one-off scripts

Capture-specific one-off scripts were moved to `scripts/archive/one_off/` to
reduce root-folder clutter while keeping reproducibility:

- `dump_new_log.py`
- `dump_new_log2.py`
- `track_status.py`
- `parse_hist_0518b.py`
- `parse_hist_raw.py`
- `debug_frames.py`
- `dump_all_frames.py`