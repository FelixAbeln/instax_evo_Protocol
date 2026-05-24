# Model quirks — Gen 1 / Gen 2 / Gen 3

← [Wiki index](README.md)

## Gen 1 — Mini Evo (FI019)

Raw per-model validation traces are tracked in
[model-quirks-evidence.md](model-quirks-evidence.md).

The Mini Evo participates in the [Link protocol](link-protocol.md) for status
queries and printing, and **does not support the `(0x88,xx)` image transfer
protocol**. Live view works, but Gen 1 often needs a short warm-up period.

### Confirmed behaviour (live tests, `FA:AB:BC:11:6F:D2`)

| Feature | Status | Notes |
|---|---|---|
| Status queries `(0x00,xx)` | ✅ Works | Battery, model, serial, photos_left all returned correctly |
| Flash control `(0x80,0x11, reg 0x0B)` | ⚠️ Partial / best-effort | Direct probes still show no reliable `(0x80,0x11)` ACK on 2026-05-21; repo app now treats FI019 flash as readback-confirmed best-effort instead of ACK-only success |
| `PRINT_HISTORY_INFO` `(0x00,0x02,[0x03])` | ✅ Works | Probe-confirmed on FI019: readable (`transfers=60`, `prints=16`); increments still to validate with print/transfer event |
| `CAMERA_HISTORY_INFO` `(0x00,0x02,[0x05])` | ✅ Works | Probe-confirmed on FI019: increments live (`84 -> 102` during 120 s shot run) |
| `CAMERA_FUNCTION_INFO` poll | ✅ Works | Flag appears (0x01) when user presses Transfer |
| **Print** (phone → camera → film ejected) | ✅ Works | Same `(0x80,xx)` print sequence as Gen 2 |
| `(0x88,00)` IMAGE_TRANSFER_START | ❌ **Camera disconnects** | Sending `(0x88,00)` causes the camera to drop the BLE link immediately |
| Live view `(0x82,xx)` | ✅ **Works (warm-up required)** | Probe-confirmed on FI019: `(0x82,00)` ACK succeeds; early `(0x82,01)` pulls may return short payload `0x02`, then valid JPEG frames follow (example run: 10 warm-up pulls then 20 valid frames). |
| `0x82` picture receive flow `(0x82,10/20/21/22)` | ✅ Works with app-style state | During active live view, standalone `(0x82,10)` got `[0xc0]` and no image. But the app-style sequence "open live view -> pull frames -> stop live view -> `(0x82,10/20/21/22)`" returned a 28,795 B JPEG on 2026-05-21. |
| `(0x84,xx)` log queries | ⏳ Not explored | — |

### FI019 transfer-watch signal (new confirmed behavior)

In current Gen 1 watcher runs (poll set `sub=0x02,0x03,0x01,0x04,0x05`), the
only repeatable transfer-related delta is in `CAMERA_FUNCTION_INFO` (`sub=0x04`):

- `payload[4]` (`ready`) remained asserted (`0x01`) throughout the observed
  sessions.
- `payload[5]` (`q_like`) incremented with user Transfer actions, observed as
  `...0101... -> ...0102... -> ...0103... -> ...0104...` across consecutive
  short watcher runs.
- Other polled fields in the same windows stayed stable:
  - `sub=0x02` `PRINTER_FUNCTION_INFO`
  - `sub=0x03` `PRINT_HISTORY_INFO`
  - `sub=0x01` `BATTERY_INFO`
  - `sub=0x05` `CAMERA_HISTORY_INFO`

Practical Gen 1 rule: when validating that Transfer queued/advanced state, use
`sub=0x04` byte `5` progression as the reliable signal in current evidence.

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

### Direct comparison to Gen 2 (FI028)

| Capability | Gen 1 FI019 | Gen 2 FI028 |
|---|---|---|
| Print `(0x10,xx)` | ✅ | ✅ |
| Live view `(0x82,00/01/02)` | ✅ Works (warm-up needed) | ✅ Stable |
| `0x82` picture receive flow `(0x82,10/20/21/22)` | ✅ Working, but state-dependent | ✅ Confirmed |
| Share pull `(0x88,xx)` | ❌ Disconnects | ✅ Confirmed |
| Counter fields `(0x00,0x02,[0x03/0x05])` | ✅ Readable | ✅ Readable |

### App implementation note

The repo app uses the `0x82` family in two distinct ways:

1. inside the live-view loop after a spontaneous camera `(0x82,0x02)` close on
  FI028-style shutter events;
2. from the live-view window's "Download Photo" button, which stops live view
  first and then runs `(0x82,0x10/0x20/0x21/0x22)` directly.

That second path is now probe-confirmed on FI019. So the corrected Gen 1 rule
is: `(0x82,0x10)` is not a generic live-view shutter opcode, but the full
app-style `0x82` receive flow works once live view has been stopped.

This is the main documentation correction from the May 21 cleanup: earlier repo
notes that treated FI019 `0x82` receive support as untested or absent are now
obsolete.

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

### Pairing / bond state

Older notes treated explicit `client.pair()` calls as part of the normal Wide
Evo connect sequence. Current repo behavior is simpler: the app connects and
subscribes without relying on a per-session `pair()` call. If Windows loses the
bond, fix that at the OS Bluetooth layer and reconnect.

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
await asyncio.sleep(1.0)             # settle before subscribing
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
