"""Android Bluetooth HCI snoop puller.

This helper pulls an Android bugreport via adb, extracts btsnoop logs,
and stores them under captures/ for protocol analysis.

Usage:
    python -m instax_lab.capture
    python -m instax_lab.capture --help
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import zipfile
from pathlib import Path


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _find_adb(user_path: str | None) -> str:
    if user_path:
        path = Path(user_path)
        if not path.exists():
            raise SystemExit(f"adb not found at: {path}")
        return str(path)

    found = shutil.which("adb")
    if found:
        return found

    candidates = [
        Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path("C:/Android/platform-tools/adb.exe"),
        Path("C:/platform-tools/adb.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise SystemExit(
        "Could not find adb.exe. Install Android Platform Tools, add adb to PATH, "
        "or pass --adb C:\\path\\to\\adb.exe"
    )


def _require_authorized_device(adb: str) -> None:
    result = _run([adb, "devices"], check=False)
    print(result.stdout)

    lines = [line.strip() for line in result.stdout.splitlines()]
    device_lines = [line for line in lines if line and not line.startswith("List of devices")]

    if any("\tunauthorized" in line for line in device_lines):
        raise SystemExit(
            "Device connected but unauthorized. Unlock the phone and accept the USB debugging prompt."
        )

    devices = [line for line in device_lines if "\tdevice" in line]
    if not devices:
        raise SystemExit(
            "No authorized Android device found. Enable USB debugging, connect USB, and authorize this PC."
        )


def _extract_btsnoop_from_zip(zip_path: Path, out_dir: Path) -> list[Path]:
    extracted: list[Path] = []

    with zipfile.ZipFile(zip_path, "r") as archive:
        names = archive.namelist()

        candidates = [name for name in names if "btsnoop" in name.lower()]
        for index, name in enumerate(candidates, start=1):
            filename = Path(name).name or f"btsnoop_{index}.log"
            out_name = filename if filename.lower().endswith(".log") else f"{filename}.log"
            out_path = out_dir / out_name
            if out_path.exists():
                out_path = out_dir / f"{index}_{out_name}"

            with archive.open(name) as src, out_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)

            print(f"Extracted: {name} -> {out_path}")
            extracted.append(out_path)

        nested_zips = [name for name in names if name.lower().endswith(".zip")]
        for nested_name in nested_zips:
            nested_tmp = out_dir / Path(nested_name).name
            with archive.open(nested_name) as src, nested_tmp.open("wb") as dst:
                shutil.copyfileobj(src, dst)

            try:
                extracted.extend(_extract_btsnoop_from_zip(nested_tmp, out_dir))
            except zipfile.BadZipFile:
                pass
            finally:
                try:
                    nested_tmp.unlink()
                except OSError:
                    pass

    return extracted


def pull_android_btsnoop(
    adb_path: str | None = None,
    out_dir: Path | None = None,
    keep_bugreport: bool = False,
) -> int:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target_dir = out_dir or (Path.cwd() / "captures" / f"android-btsnoop-{timestamp}")
    target_dir.mkdir(parents=True, exist_ok=True)

    adb = _find_adb(adb_path)
    _require_authorized_device(adb)

    bugreport_path = target_dir / f"bugreport-{timestamp}.zip"

    print("\nCreating Android bugreport. This can take a while.")
    print("Keep the phone unlocked if Android asks for confirmation.\n")

    result = _run([adb, "bugreport", str(bugreport_path)], check=False)
    print(result.stdout)

    if not bugreport_path.exists():
        possible = sorted(target_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if possible:
            bugreport_path = possible[0]
        else:
            raise SystemExit(f"Bugreport zip was not created at: {bugreport_path}")

    print(f"\nBugreport saved: {bugreport_path}")
    print("Searching for btsnoop logs...")

    extracted = _extract_btsnoop_from_zip(bugreport_path, target_dir)
    if not extracted:
        print("\nNo btsnoop log found.")
        print("Make sure Bluetooth HCI snoop logging is enabled, then toggle Bluetooth or reboot.")
        return 2

    if not keep_bugreport:
        try:
            bugreport_path.unlink()
            print(f"Deleted full bugreport zip: {bugreport_path}")
        except OSError as exc:
            print(f"Could not delete bugreport zip: {exc}")

    print("\nDone. Open these files in Wireshark:")
    for path in extracted:
        print(f"  {path}")

    print("\nUseful Wireshark filters:")
    print("  btatt")
    print("  btatt.opcode")
    print("  btatt.value")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull Android Bluetooth HCI snoop logs via adb bugreport.")
    parser.add_argument("--adb", help="Path to adb.exe")
    parser.add_argument("--out", help="Output folder")
    parser.add_argument("--keep-bugreport", action="store_true", help="Keep the full bugreport zip")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else None
    return pull_android_btsnoop(adb_path=args.adb, out_dir=out_dir, keep_bugreport=args.keep_bugreport)


if __name__ == "__main__":
    raise SystemExit(main())
