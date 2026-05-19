# Model quirks — Gen 1 / Gen 2 / Gen 3

← [Wiki index](README.md)

## Gen 1 — Mini Evo (FI019)

The Mini Evo participates in the [Link protocol](link-protocol.md) for status
queries and printing, but **does not support the `(0x88,xx)` image transfer
protocol** and has only partial live view support.

### Confirmed behaviour (live tests, `FA:AB:BC:11:6F:D2`)

| Feature | Status | Notes |
|---|---|---|
| Status queries `(0x00,xx)` | ✅ Works | Battery, model, serial, photos_left all returned correctly |
| `CAMERA_FUNCTION_INFO` poll | ✅ Works | Flag appears (0x01) when user presses Transfer |
| **Print** (phone → camera → film ejected) | ✅ Works | Same `(0x80,xx)` print sequence as Gen 2 |
| `(0x88,00)` IMAGE_TRANSFER_START | ❌ **Camera disconnects** | Sending `(0x88,00)` causes the camera to drop the BLE link immediately |
| Live view `(0x82,xx)` | ⚠️ **Partial** | Frames received in initial tests (same `(0x82,00/01/02)` framing as Gen 2) but subsequently failed to maintain a stable session. Root cause unknown — may be timing, pairing, or firmware. |
| Auto-transfer after shutter `(0x82,10/20/21/22)` | ❓ Not tested | Unknown whether Gen 1 supports this after a live-view shutter |
| `(0x84,xx)` log queries | ⏳ Not explored | — |

### `(0x88,xx)` not supported on Gen 1

When the Mini Evo shows `CAMERA_FUNCTION_INFO` flag = `0x01` and `(0x88,00)` is
sent:
- Camera sends a BLE disconnect event with no error response
- No `(0x88,00)` ACK is ever sent
- Subsequent reconnect succeeds, but flag may still be `0x01` — **do not retry
  `(0x88,00)`**

**Detection strategy:** time out on the `(0x88,00)` response with a short
timeout (e.g. 5 s). If no response, set a `_transfer_supported = False` flag
and skip all further `(0x88,xx)` attempts in the current session. The camera
remains usable for live view and status polling.

Gen 1 presumably uses a different mechanism to transfer images to a phone
(possibly Wi-Fi or a separate app flow not captured in these sessions). The
`(0x88,xx)` opcodes may be Gen 2+ only.

### Mini Evo — transfer-mode BLE quirks

When the user presses the camera's share button, the Mini Evo enters **transfer
mode** and advertises differently. Connection in this state has two critical
differences from a normal print session:

1. **GATT handle layout shifts**: The write characteristic moves from h=0x0014
   to h=0x0014–0x0016 range; exact handles may differ between camera restarts.
   Use UUID-based lookup rather than hard-coded handles.
2. **Do NOT call `pair()`**: In transfer mode the camera accepts the BLE
   connection but immediately disconnects if `pair()` is called. The WinRT BLE
   stack uses cached bond keys automatically (the camera was previously paired
   in normal mode), so no explicit pairing call is needed.
3. **Wait 2 seconds after connect** before subscribing to the notify CCCD.
   Attempting to write CCCD too soon after connection causes "Attribute not
   found" errors as the camera is still setting up its GATT table.
4. **Camera may advertise with `name=None`** (no advertising name) in
   mid-state-transition. Scan by address once the name is confirmed.

```python
# Transfer-mode connect pattern (no pair() call):
dev = await BleakScanner.find_device_by_filter(
    lambda d, a: d.address.upper() == CAMERA_ADDR.upper(), timeout=30
)
client = BleakClient(dev, timeout=30)
await client.connect()
await asyncio.sleep(2.0)          # settle BEFORE subscribing
await client.start_notify(NOTIFY_UUID, handler)
```

## Gen 2 — Evo Wide (FI028)

The Wide Evo (`FA:AB:BC:1D:0A:7B`) uses the same Link protocol but has several
connection-level differences from the Mini Evo:

### Advertising name

`INSTAX-[serial](BLE)` — note the `(BLE)` suffix, **not** `(IOS)`. Despite
this, the camera uses the same Link service UUID and identical protocol
framing. The `(BLE)` label appears to be a firmware artifact, not an indicator
of the Android profile.

### Pairing required every session

Wide Evo does not retain bond state across connections the way Mini Evo does.
Call `await client.pair()` after connecting and before writing CCCD. The
pairing completes with a simple PIN confirmation dialog (no real PIN — just
click through). `pair()` may raise `"OPERATION_ALREADY_IN_PROGRESS"` on
retries; treat as non-fatal and wait ~3 s before proceeding.

### Windows interference quirk

If the Mini Evo (`FA:AB:BC:11:6F:D2`) is listed in Windows Bluetooth devices,
its bond record interferes with Wide Evo pairing and causes repeated `pair()`
failures. **Remove the Mini Evo from Windows Bluetooth settings** before
pairing the Wide Evo.

### GATT handles

Write h=0x0010, Notify h=0x0012, CCCD h=0x0013 (differs from Mini Evo's
h=0x0014/0x0016). MTU negotiates to 247 bytes.

### `LIVE_VIEW_PREPARE` (0x80,0x15) response

Wide Evo returns 17 bytes: `[8×0x00][0x32][0x01][7×0x00]` — byte[8] = `0x32` =
50 (meaning TBD; matches `CAMERA_FUNCTION_INFO` byte[1]). Mini Evo returns
1 byte `[0xBF]`.

### `LIVE_VIEW_FRAME` delivery

Wide Evo delivers the full JPEG in a single ATT burst (one `(0x82,0x01)` pull)
rather than requiring 2 pulls like the Mini Evo.

```python
# Wide Evo connect pattern:
dev = await BleakScanner.find_device_by_filter(
    lambda d, a: d.address.upper() == "FA:AB:BC:1D:0A:7B", timeout=30
)
client = BleakClient(dev, timeout=30)
await client.connect()
try:
    await client.pair()
except Exception:
    pass                              # non-fatal; camera may already be pairing
await asyncio.sleep(3.0)             # settle after pair before subscribing
await client.start_notify(NOTIFY_UUID, handler)
```

## Gen 3 — Mini Evo Cinema

Not in our possession. Assumed to use the same Link protocol. Known facts:

- Same physical instax mini film cartridge as the original Mini Evo.
- Prints in **landscape** orientation (800 × 600 px) by rotating the print head
  direction.
- The native camera print mode uses the full film strip (1600 × 600 dots),
  while smartphone print uses half (800 × 600). The camera will report
  whichever dimension applies to the current print mode via
  `IMAGE_SUPPORT_INFO` — always read this rather than hard-coding.

## Known film counts (confirmed)

| Camera | Film remaining | Source |
|---|---|---|
| Gen 1 Mini Evo (FI019) | 1 shot | `PRINTER_FUNCTION_INFO` response[8] & 0x0F = 1 ✓ live |
| Gen 1 Mini Evo (FI019) | 1 shot | Android protocol `16 02` response byte[2] (cross-check) |
| Gen 2 Evo Wide (FI028) | 6 shots | `PRINTER_FUNCTION_INFO` status=0x26, 0x26 & 0x0F = 6 (HCI log, keepalive) |
