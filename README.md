# instax-evo-lab-win

Windows + VS Code starter project for exploring the instax mini Evo / Link-style BLE print protocol.

## What this does now

- Scan for nearby BLE devices
- Inspect GATT services/characteristics
- Subscribe to notifications
- Log writes/notifications in JSONL format
- Provide a place to document the protocol as we learn it

## Requirements

- Windows 10/11
- Python 3.11+
- VS Code
- Bluetooth adapter
- Android phone for official-app HCI snoop capture
- instax mini Evo or compatible instax device

## Setup in VS Code

Open this folder in VS Code, then in the terminal:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then retry:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Commands

Scan:

```powershell
python -m instax_lab scan --timeout 10
```

Inspect a device:

```powershell
python -m instax_lab inspect "AA:BB:CC:DD:EE:FF"
```

Subscribe to all notify characteristics and log notifications:

```powershell
python -m instax_lab notify "AA:BB:CC:DD:EE:FF"
```

Replay a JSONL write capture:

```powershell
python -m instax_lab replay "AA:BB:CC:DD:EE:FF" captures\sample-writes.jsonl
```

Pull Android HCI snoop logs via adb bugreport:

```powershell
python -m instax_lab.capture --help
python -m instax_lab.capture
```

Extract btsnoop logs from existing Android zip captures and delete source zips:

```powershell
python -m instax_lab extract-captures captures
python -m instax_lab extract-captures captures --keep-source
```

## Notes

On Windows, BLE addresses may appear as UUID-like device IDs instead of MAC addresses. Use exactly the address/device ID printed by the scan command.

For official app protocol captures, use Android Bluetooth HCI snoop logging and open the resulting `btsnoop_hci.log` in Wireshark.
