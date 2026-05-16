# Captures

Put local captures here.

Recommended names:

- android-official-white.btsnoop
- android-official-black.btsnoop
- android-official-checkerboard.btsnoop
- python-inspect-log.txt
- notifications.jsonl

Do not publish raw captures without checking for private data:
Bluetooth addresses, phone identifiers, image content, pairing/session material.

## Extract and clean zip captures

If you have Android bugreport zip files in this folder, extract only btsnoop logs and remove the source zips:

```powershell
python -m instax_lab extract-captures captures
```

Keep source zips if needed:

```powershell
python -m instax_lab extract-captures captures --keep-source
```

Preview actions only:

```powershell
python -m instax_lab extract-captures captures --dry-run
```
