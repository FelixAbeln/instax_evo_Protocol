#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

SAFE_EXTS = {".md", ".txt", ".json", ".jsonl", ".log", ".py", ".csv"}
MAX_TEXT_BYTES = 2_000_000

MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\r\n\t\x00]*")
LONG_ID_RE = re.compile(r"\b\d{8,}\b")


def looks_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        try:
            data.decode("latin-1")
            return True
        except UnicodeDecodeError:
            return False


def redact_text(text: str) -> str:
    text = MAC_RE.sub("<REDACTED_MAC>", text)
    text = WIN_PATH_RE.sub("<REDACTED_PATH>", text)
    text = LONG_ID_RE.sub("<REDACTED_ID>", text)
    return text


def sanitize_tree(src: Path, dst: Path) -> dict[str, int]:
    counts = {
        "copied": 0,
        "skipped_ext": 0,
        "skipped_size": 0,
        "skipped_binary": 0,
        "failed": 0,
    }

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    for p in src.rglob("*"):
        if p.is_dir():
            continue

        rel = p.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)

        if p.suffix.lower() not in SAFE_EXTS:
            counts["skipped_ext"] += 1
            continue

        try:
            raw = p.read_bytes()
            if len(raw) > MAX_TEXT_BYTES:
                counts["skipped_size"] += 1
                continue
            if not looks_text(raw):
                counts["skipped_binary"] += 1
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")

            clean = redact_text(text)
            out.write_text(clean, encoding="utf-8")
            counts["copied"] += 1
        except Exception:
            counts["failed"] += 1

    return counts


def _safe_move_file(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    src.replace(dst)


def organize_tree(dst: Path) -> None:
    # Standard folders used by docs/evidence links.
    for d in [
        dst / "favorites" / "flows",
        dst / "favorites" / "snapshots",
        dst / "analysis" / "logs",
        dst / "analysis" / "traces",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # Favorites snapshots moved under favorites/snapshots.
    old_snap = dst / "favorites_snapshots"
    if old_snap.exists() and old_snap.is_dir():
        for f in old_snap.glob("*.json"):
            _safe_move_file(f, dst / "favorites" / "snapshots" / f.name)

    # Favorites flow files moved under favorites/flows.
    fav_root = dst / "favorites"
    if fav_root.exists() and fav_root.is_dir():
        for f in fav_root.glob("*flow*.txt"):
            _safe_move_file(f, dst / "favorites" / "flows" / f.name)

    # Common top-level logs moved under analysis/logs.
    for pat in [
        "queue_increment_watch_*.log",
        "share_flag_watch_*.log",
        "history-listen.jsonl",
        "sample-writes.jsonl",
        "print-log.jsonl",
    ]:
        for f in dst.glob(pat):
            _safe_move_file(f, dst / "analysis" / "logs" / f.name)

    # trace_compare folder moved under analysis/traces/trace_compare.
    old_trace = dst / "trace_compare"
    new_trace = dst / "analysis" / "traces" / "trace_compare"
    if old_trace.exists() and old_trace.is_dir():
        if new_trace.exists():
            shutil.rmtree(new_trace)
        new_trace.parent.mkdir(parents=True, exist_ok=True)
        old_trace.replace(new_trace)


def remove_empty_artifacts(dst: Path) -> dict[str, int]:
    removed_files = 0
    removed_dirs = 0

    for f in dst.rglob("*"):
        if f.is_file() and f.stat().st_size == 0:
            f.unlink()
            removed_files += 1

    while True:
        empty_dirs = [
            d for d in dst.rglob("*")
            if d.is_dir() and not any(d.iterdir())
        ]
        if not empty_dirs:
            break
        for d in empty_dirs:
            d.rmdir()
            removed_dirs += 1

    return {
        "removed_empty_files": removed_files,
        "removed_empty_dirs": removed_dirs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Sanitize captures into a share-safe tree")
    ap.add_argument("--src", default="local_files/raw_captures", help="Source raw captures directory")
    ap.add_argument("--dst", default="captures", help="Destination sanitized captures directory")
    ap.add_argument("--report", default="captures/sanitization_report.json", help="Output report JSON path")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    report_path = Path(args.report)

    if not src.exists():
        raise FileNotFoundError(f"source directory not found: {src}")

    counts = sanitize_tree(src, dst)
    organize_tree(dst)
    cleanup = remove_empty_artifacts(dst)

    report = {
        "source": str(src),
        "destination": str(dst),
        "counts": counts,
        "cleanup": cleanup,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    readme = dst / "README.md"
    readme.write_text(
        "# Sanitized captures\n\n"
        "This folder contains share-safe sanitized artifacts generated from local raw captures.\n\n"
        "- Raw/non-share-safe artifacts are stored under local_files/raw_captures (gitignored).\n"
        "- Regenerate this folder with scripts/sanitize_captures.py.\n"
        "- Redactions applied: MAC addresses, long numeric IDs, Windows absolute paths.\n"
        "- Binary and unsupported file types are excluded.\n",
        encoding="utf-8",
    )

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
