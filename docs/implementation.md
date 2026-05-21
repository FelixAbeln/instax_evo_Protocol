# Implementation notes — Windows / bleak

← [Wiki index](README.md)

These quirks apply when using the [bleak](https://github.com/hbldh/bleak)
library on Windows (WinRT BLE backend). They do **not** affect macOS/Linux
CoreBluetooth/BlueZ.

## MTU and bonding

| MTU at connect | Meaning | Action required |
|---|---|---|
| 247 | Device is bonded (paired) | None — `start_notify` will succeed |
| 23 (default) | Session/bonding state not ready yet | Reconnect after ensuring the OS-level bond exists |

Older probe helpers called `client.pair()` here. The maintained app flow now
avoids treating that as a required reconnect step and instead relies on the
existing Windows bond plus a fresh client + retry logic.

## Post-disconnect GATT "Characteristic not found"

After a camera-initiated disconnect (e.g. the camera drops BLE due to an
unknown command), Windows re-uses the stale GATT service cache from the
previous connection. A new `BleakClient` created from a `BLEDevice` object
(returned by a fresh scan) inherits this cache and fails with:

```
BleakError: Characteristic 70954784-2d83-473d-9e5f-81e1d02d5273 was not found!
```

**Fix:** Create `BleakClient` from the **address string**, not from the
`BLEDevice` object:

```python
# ❌ Do NOT do this — reuses cached GATT table
dev = await BleakScanner.find_device_by_address(address)
client = BleakClient(dev, timeout=30)

# ✅ Do this — forces fresh service discovery
client = BleakClient(address, timeout=30)
```

## Reliable subscribe sequence

```python
await client.connect()
await asyncio.sleep(1.0)   # wait for GATT cache to populate

# Retry start_notify up to 3 times with 2 s delay
for attempt in range(1, 4):
    try:
        await client.start_notify(NOTIFY_UUID, callback)
        break
    except Exception:
        if attempt == 3:
            raise
        await asyncio.sleep(2.0)
```

## Reconnect after camera-initiated disconnect

After detecting connection loss (notify callback stops arriving or bleak
raises):

1. Call `await client.disconnect()` and set `client = None` (clears stale state).
2. Wait ≥ 5 s (camera BLE stack needs time to re-advertise after self-disconnect).
3. Scan for the device by address string and create a **new**
   `BleakClient(address, …)`.
4. Run the reliable subscribe sequence above.

If the camera disconnected because of an unsupported command (e.g. `(0x88,00)`
on Gen 1), set a flag to skip that command on reconnect — otherwise the cycle
repeats. Sequence counter is global across BLE connection sessions.

## Connection notes

- Gen 1 may require a one-time Windows Bluetooth pairing step after firmware or
  bond resets.
- The maintained app flow does not rely on explicit `client.pair()` calls on
  every session.
- If the camera's firmware was updated, its bond database is wiped. Remove the
  INSTAX entry from Windows Bluetooth settings and re-pair.
- BLE device name format: `INSTAX-[serial] (IOS)` (Mini Evo) /
  `INSTAX-[serial](BLE)` (Wide Evo); serial matches `DEVICE_INFO_SERVICE`
  InfoType=2. Filter by service UUID `70954782-…`, not by name suffix.

## Capture log files

| File | Camera | Profile | Notes |
|---|---|---|---|
| `captures/extracted/.../17-34-32/btsnoop_hci.log` | Gen 1 Mini Evo | **Android** | Full print session decoded; battery + film count confirmed |
| `captures/extracted/19-51-52/FS/data/log/bt/btsnoop_hci.log` | Gen 2 Evo Wide | **Link** | 4 identical BLE connections; full Link protocol decoded |
| `captures/extracted/19-51-52/.../btsnoop_hci.log.last` | Mixed | — | Also contains BR/EDR traffic from an Instax printer |

## Local print log

Every `evo-print` run appends a record to `captures/print-log.jsonl`:

```json
{
  "t": 1747397000.0,
  "image": "F:\\path\\to\\image.jpg",
  "camera": "FA:AB:BC:11:6F:D2",
  "model": "FI019",
  "transferred": true,
  "printed": false,
  "photos_left_after": 1
}
```

| Field | Meaning |
|---|---|
| `t` | Unix timestamp of the operation |
| `image` | Absolute path to the source image file |
| `camera` | BLE address of the camera (Link profile) |
| `model` | Model ID from `DEVICE_INFO_SERVICE` (e.g. `"FI019"`) |
| `transferred` | `true` if image data was fully sent to camera |
| `printed` | `true` if `PRINT_IMAGE` (0x10,0x80) was also sent (film ejected) |
| `photos_left_after` | `photos_left` value from post-print status poll |

`transferred=true, printed=false` means `--enable-print` was not passed — image
was sent but film was not ejected (safe test mode).
