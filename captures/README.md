# Sanitized captures

This folder contains share-safe sanitized artifacts generated from local raw captures.

- Raw/non-share-safe artifacts are stored under local_files/raw_captures (gitignored).
- Regenerate this folder with scripts/sanitize_captures.py.
- Redactions applied: MAC addresses, long numeric IDs, Windows absolute paths.
- Binary and unsupported file types are excluded.
- The sanitizer script also enforces folder organization and removes empty
	files/directories on each run.

## Organization

- `favorites/flows/`:
	- sanitized favorites write/read flow excerpts
	- file naming: `<slot>_<action>_flow_sanitized.txt`
- `favorites/snapshots/`:
	- JSON slot snapshots from live dump tooling
	- file naming: `favorites_slots_YYYYMMDD_HHMMSS.json`
- `analysis/logs/`:
	- queue/share/history observation logs and JSONL traces
- `analysis/traces/`:
	- trace-comparison artifacts used by protocol notes
- topic/source folders such as `new_log_0517b/`, `new_capture_0518/`,
	`extracted/`, `bugreport_*`:
	- sanitized source-derived artifacts kept for reproducibility

## Hygiene rules

- Empty files and empty directories are removed.
- Do not put raw private artifacts here; use `local_files/raw_captures/`.
- Evidence docs in `docs/*-evidence.md` should link to paths inside this folder.
