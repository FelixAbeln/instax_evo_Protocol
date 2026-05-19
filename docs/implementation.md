# Implementation notes — Windows / bleak

← [Wiki index](README.md)

These quirks apply when using the [bleak](https://github.com/hbldh/bleak)
library on Windows (WinRT BLE backend). They do **not** affect macOS/Linux
CoreBluetooth/BlueZ.

## MTU and bonding

| MTU at connect | Meaning | Action required |
|---|---|---|
| 247 | Device is bonded (paired) | None — `start_notify` will succeed |
| 23 (default) | Not bonded | Call `client.pair()` before `start_notify()` |

Gen 1 cameras require pairing even on reconnect. After `pair()`, sleep ≥ 3 s
before calling `start_notify()` — the GATT cache on Windows may not yet be
populated.

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

## Reliable subscribe sequence (Gen 1)

```python
await client.connect()
await client.pair()
await asyncio.sleep(3.0)   # wait for GATT cache to populate

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

- **Link profile requires passkey/PIN pairing** (at least on Gen 1 after
  firmware update). The user must pair once via Windows Bluetooth settings (a
  6-digit code appears on screen or in the app).
- After pairing, call `client.pair()` in bleak before subscribing — this
  re-establishes the encrypted session for the current connection. Without it,
  CCCD writes fail with "Operation aborted".
- `pair()` returns `None` when already bonded (correct — the encrypted session
  is still established).
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
