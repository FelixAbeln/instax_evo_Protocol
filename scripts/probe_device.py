"""
Probe an Instax camera over its IOS BLE profile using the Instax Link protocol.

Protocol summary (javl/InstaxBLE compatible):
  Packet:  [41 62] [total_len: uint16 BE] [op1] [op2] [payload...] [checksum]
  Response:[61 42] [total_len: uint16 BE] [op1] [op2] [payload...] [checksum]
  Checksum = (255 - sum(all preceding bytes)) & 255

All cameras have two BLE profiles:
  IOS    (FA:AB:BC:...) — Link protocol, single channel, NO pairing needed  ← USE THIS
  Android(E0:48:24:...) — legacy 16xx/17xx binary, two channels, requires pairing

Key EventType op codes:
  (0x00, 0x00) SUPPORT_FUNCTION_AND_VERSION_INFO  — hello/init
  (0x00, 0x01) DEVICE_INFO_SERVICE + InfoType byte — manufacturer, model, serial, ...
  (0x00, 0x02) SUPPORT_FUNCTION_INFO  + InfoType byte — battery / film count
  (0x20, 0x10) FW_PROGRAM_INFO        — firmware version
  (0x84, 0x00) CAMERA_LOG_SUBTOTAL_START — film remaining (Evo cameras)

InfoType values (payload byte):
  0x00 IMAGE_SUPPORT_INFO   → (w, h) uint16 BE — determines film size
  0x01 BATTERY_INFO         → [state, pct] battery level
  0x02 PRINTER_FUNCTION_INFO→ status byte: low4=photosLeft, bit7=charging
"""
import asyncio
import struct
from bleak import BleakScanner, BleakClient

# Shared Instax GATT service UUIDs (same across all known models and both BLE profiles)
WRITE_UUID  = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"

# Known BLE addresses
IOS_ADDR     = "FA:AB:BC:11:6F:D2"  # Gen 1 Mini Evo  — IOS profile   ← PREFERRED
ANDROID_ADDR = "E0:48:24:D7:CF:2E"  # Gen 1 Mini Evo  — Android profile (old protocol)
NEW_CAM_ADDR = "FA:AB:BC:1D:0A:7B"  # Gen 2 Evo Wide  — IOS profile   ← PREFERRED

# Android profile: device-specific DEVICE_ID from 17-34-32 HCI capture
DEVICE_ID = bytes([0x8d, 0x3d, 0xb0, 0xe5, 0x92, 0x59, 0x03, 0x3d])

GENERIC_ATTR_SVC = "00001801-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# Link protocol packet builder (javl/InstaxBLE compatible)
# ---------------------------------------------------------------------------

def create_packet(op1: int, op2: int, payload: bytes = b'') -> bytes:
    """Build an Instax Link protocol request packet.

    Format: [41 62] [total_len: uint16 BE] [op1] [op2] [payload...] [checksum]
    total_len = 7 + len(payload); checksum = (255 - sum(preceding bytes)) & 255
    """
    header = b'\x41\x62'
    length = struct.pack('>H', 7 + len(payload))
    body   = header + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])


def validate_checksum(packet: bytes) -> bool:
    return (sum(packet) & 255) == 255


def decode_response(raw: bytes) -> dict:
    """Decode an Instax Link response notification into a dict."""
    if len(raw) < 7:
        return {"raw": raw.hex(), "error": "too short"}
    if raw[:2] != b'\x61\x42':
        return {"raw": raw.hex(), "error": "bad header"}
    if not validate_checksum(raw):
        return {"raw": raw.hex(), "error": "bad checksum"}
    total_len = struct.unpack_from('>H', raw, 2)[0]
    op1, op2  = raw[4], raw[5]
    payload   = raw[6:total_len - 1]   # strip header(2)+len(2)+op1+op2+cs(1)
    return {"op": (op1, op2), "payload": payload, "raw": raw.hex()}



# ---------------------------------------------------------------------------
# IOS profile handler — Link protocol, works for all known models
# ---------------------------------------------------------------------------

async def run_probe_ios(client: "BleakClient", responses: list):
    """Probe an Instax camera via its IOS BLE profile using the Link protocol.

    Works for Gen 1 (Mini Evo) and Gen 2 (Evo Wide). No pairing required.
    """
    all_services = list(client.services)
    print(f"MTU: {client.mtu_size}  Services: {len(all_services)}")

    for svc in all_services:
        print(f"  SVC {svc.uuid}")
        for char in svc.characteristics:
            props = ",".join(char.properties)
            print(f"    h=0x{char.handle:04X}  {char.uuid[:8]}  {props}")
            for desc in char.descriptors:
                print(f"      desc h=0x{desc.handle:04X}  {desc.uuid[:8]}")

    def on_notify(sender, data: bytearray):
        data = bytes(data)
        tag  = f"0x{sender:04X}" if isinstance(sender, int) else str(sender)
        dec  = decode_response(data)
        op   = dec.get("op")
        payload = dec.get("payload", b"")
        print(f"  <-- NOTIFY {tag}: [{len(data)}] {data.hex()}")

        if "error" in dec:
            print(f"      *** decode error: {dec['error']}")
            responses.append((op, payload, data))
            return

        # Response payload format for SUPPORT_FUNCTION_INFO and DEVICE_INFO_SERVICE:
        #   [0x00] [InfoType_echo: 1B] [actual_data...]
        # This matches javl's packet[8:10] indexing (raw bytes 8-9 = payload bytes 2-3).
        info_type = payload[1] if len(payload) >= 2 else -1
        data_payload = payload[2:] if len(payload) >= 2 else b''

        # SUPPORT_FUNCTION_INFO (op=0x00, 0x02)
        if op == (0x00, 0x02):
            if info_type == 0x01 and len(data_payload) >= 2:  # BATTERY_INFO
                state, pct = data_payload[0], data_payload[1]
                level = {0: "CRITICAL", 1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "FULL"}.get(state, f"?({state})")
                print(f"      *** BATTERY state={state} ({level}), pct={pct}%")
            elif info_type == 0x02 and len(data_payload) >= 1:  # PRINTER_FUNCTION_INFO
                status = data_payload[0]
                photos = status & 0x0F
                charging = bool(status & 0x80)
                print(f"      *** PRINTER: photos_left={photos}, charging={charging}")
            elif info_type == 0x00 and len(data_payload) >= 4:  # IMAGE_SUPPORT_INFO
                w, h = struct.unpack_from('>HH', data_payload, 0)
                print(f"      *** IMAGE_SUPPORT: {w}x{h}")
            else:
                print(f"      *** SUPPORT_FUNCTION_INFO InfoType={info_type:#04x}: {data_payload.hex()}")

        # DEVICE_INFO_SERVICE (op=0x00, 0x01) — payload: [00][InfoType][length][text]
        elif op == (0x00, 0x01):
            names = {0: "Manufacturer", 1: "Model", 2: "Serial", 3: "Field3",
                     4: "Field4", 5: "Field5", 9: "Field9", 10: "Field10"}
            label = names.get(info_type, f"InfoType={info_type}")
            if len(data_payload) >= 1:
                str_len = data_payload[0]
                text = data_payload[1:1 + str_len].decode("ascii", errors="replace")
                print(f"      *** DEVICE_INFO {label}: {text!r}")

        # Film log: op=(0x84, 0x00) — CAMERA_LOG_SUBTOTAL_START
        # Returns lifetime shot counts, NOT remaining shots.
        # Use PRINTER_FUNCTION_INFO (0x00,0x02 InfoType=2) for shots remaining.
        elif op == (0x84, 0x00) and len(payload) >= 12:
            a = struct.unpack_from('<I', payload, 0)[0]
            b = struct.unpack_from('<I', payload, 4)[0]
            c = struct.unpack_from('<I', payload, 8)[0]
            print(f"      *** CAMERA_LOG: val[0]={a} val[1]={b} val[2]={c} (lifetime counts)")

        # Firmware version: op=(0x20, 0x10)
        elif op == (0x20, 0x10):
            print(f"      *** FW_INFO: {payload.hex()}")

        # Hello: op=(0x00, 0x00)
        elif op == (0x00, 0x00):
            print(f"      *** HELLO_RSP: {payload.hex()}")

        else:
            print(f"      op=({op[0]:#04x},{op[1]:#04x}) payload={payload.hex()!r}")

        responses.append((op, payload, data))

    # Find write and notify chars by UUID (handle-independent)
    wc = client.services.get_characteristic(WRITE_UUID)
    nc = client.services.get_characteristic(NOTIFY_UUID)

    if not wc or not nc:
        print("ERROR: Could not find Instax write/notify characteristics by UUID.")
        print("Is this the IOS BLE profile? (name should end in '(IOS)')")
        return

    print(f"\nWrite char:  h=0x{wc.handle:04X}  {wc.uuid[:8]}")
    print(f"Notify char: h=0x{nc.handle:04X}  {nc.uuid[:8]}")

    # Establish encrypted session — required if camera uses authenticated pairing.
    # pair() is safe to call even when already bonded; it triggers the security handshake.
    print("Pairing / establishing encrypted session ...")
    try:
        result = await client.pair()
        print(f"  pair() → {result!r}  (True=fresh, False=failed, None=already bonded)")
    except Exception as e:
        print(f"  pair() exception: {e} — continuing anyway")
    await asyncio.sleep(1.0)

    # Subscribe to notify char
    try:
        await asyncio.wait_for(client.start_notify(nc.uuid, on_notify), timeout=5.0)
        print(f"Subscribed to h=0x{nc.handle:04X}")
    except asyncio.TimeoutError:
        print(f"Subscribe timed out — continuing anyway")
    except Exception as e:
        print(f"Subscribe failed: {e}")
        if "not connected" in str(e).lower() or "aborted" in str(e).lower():
            print("  Camera disconnected during subscribe — check pairing in Windows BT settings.")
            return

    async def wr(op1: int, op2: int, payload: bytes = b'', label: str = ""):
        pkt = create_packet(op1, op2, payload)
        desc = label or f"op=({op1:#04x},{op2:#04x}) payload={payload.hex()!r}"
        print(f"--> {desc}  [{len(pkt)}B] {pkt.hex()}")
        before = len(responses)
        try:
            await client.write_gatt_char(wc.uuid, bytearray(pkt), response=False)
        except Exception as e:
            print(f"    write error: {e}")
            return
        await asyncio.sleep(0.5)
        if len(responses) > before:
            print(f"    ^^^ GOT {len(responses)-before} notification(s)!")

    print("\n--- Link protocol init sequence ---")
    await wr(0x00, 0x00, label="SUPPORT_FUNCTION_AND_VERSION_INFO (hello)")

    print("\n--- Device info ---")
    await wr(0x00, 0x01, b'\x00', label="DEVICE_INFO IMAGE_SUPPORT_INFO → film size")
    await wr(0x00, 0x01, b'\x01', label="DEVICE_INFO BATTERY_INFO → manufacturer")
    await wr(0x00, 0x01, b'\x02', label="DEVICE_INFO PRINTER_FUNCTION_INFO → model")
    await wr(0x00, 0x01, b'\x03', label="DEVICE_INFO PRINT_HISTORY_INFO → serial")

    print("\n--- Firmware version ---")
    await wr(0x20, 0x10, label="FW_PROGRAM_INFO")

    print("\n--- Battery and film count (SUPPORT_FUNCTION_INFO) ---")
    await wr(0x00, 0x02, b'\x00', label="SUPPORT_FUNCTION_INFO IMAGE_SUPPORT_INFO")
    await wr(0x00, 0x02, b'\x01', label="SUPPORT_FUNCTION_INFO BATTERY_INFO")
    await wr(0x00, 0x02, b'\x02', label="SUPPORT_FUNCTION_INFO PRINTER_FUNCTION_INFO → photos left")

    print("\n--- Film remaining (Evo cameras) ---")
    await wr(0x84, 0x00, label="CAMERA_LOG_SUBTOTAL_START → film remaining")

    print(f"\n--- Listening 20s for additional notifications ---")
    before = len(responses)
    await asyncio.sleep(20.0)
    print(f"Received {len(responses)-before} extra packets")

    print(f"\nAll responses ({len(responses)} total):")
    for op, payload, raw in responses:
        print(f"  op=({op[0]:#04x},{op[1]:#04x}) payload={payload.hex()!r}")


# ---------------------------------------------------------------------------
# Android profile handler (legacy, for reference only — not normally used)
# ---------------------------------------------------------------------------

async def run_probe_android(client: "BleakClient", responses: list):
    """Legacy Android profile handler (16xx/17xx binary protocol).

    Only works on Android BLE profile (E0:48:24:...). Kept for reference.
    """
    HANDSHAKE_12A = bytes([0x00, 0x05]) + DEVICE_ID + bytes([0x00, 0x00])
    HANDSHAKE_13A = bytes([0x00, 0x00]) + DEVICE_ID + bytes([0x04, 0x00, 0x00])
    HANDSHAKE_12B = bytes([0x00, 0x05, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    HANDSHAKE_13B = bytes([0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00])

    all_services = list(client.services)
    print(f"MTU: {client.mtu_size}  Services: {len(all_services)}")

    notify_chars, write_chars = [], []
    for svc in all_services:
        print(f"  SVC {svc.uuid}")
        for char in svc.characteristics:
            props = ",".join(char.properties)
            print(f"    h=0x{char.handle:04X}  {char.uuid[:8]}  {props}")
            for desc in char.descriptors:
                print(f"      desc h=0x{desc.handle:04X}  {desc.uuid[:8]}")
            if "notify" in char.properties or "indicate" in char.properties:
                notify_chars.append(char)
            if "write" in char.properties or "write-without-response" in char.properties:
                write_chars.append(char)

    def on_notify(sender, data):
        data = bytes(data)
        tag  = f"0x{sender:04X}" if isinstance(sender, int) else str(sender)
        print(f"  <-- NOTIFY {tag}: [{len(data)}] {data.hex()}")
        if data[:2] == b'\x16\x01' and len(data) >= 5:
            batt = data[3]
            lvl  = {3: "HIGH", 2: "MED", 1: "LOW", 0: "CRIT"}.get(batt, f"?({batt})")
            print(f"      *** BATTERY = {batt} ({lvl})")
        elif data[:2] == b'\x16\x02' and len(data) >= 4:
            count = data[2]
            print(f"      *** FILM REMAINING ≈ {count}")
        responses.append((sender, data.hex()))

    # Pair — required for Android profile; None = already bonded (may cause disconnect)
    print("\n--- Pairing / bonding (Android profile requires pairing) ---")
    print("    If pair() returns None and printer disconnects, remove the INSTAX")
    print("    device from Windows Bluetooth settings and retry for a fresh pair.")
    try:
        paired = await client.pair()
        print(f"Pair result: {paired}  (None = already paired, True = fresh pair)")
    except Exception as e:
        print(f"Pair attempt exception: {e}")
    await asyncio.sleep(1.0)

    instax_notify = [nc for nc in notify_chars if nc.service_uuid != GENERIC_ATTR_SVC]
    for nc in instax_notify:
        try:
            await asyncio.wait_for(client.start_notify(nc.uuid, on_notify), timeout=5.0)
            print(f"Subscribed h=0x{nc.handle:04X}")
        except asyncio.TimeoutError:
            print(f"Subscribe h=0x{nc.handle:04X} timed out")
        except Exception as e:
            print(f"Subscribe h=0x{nc.handle:04X} failed: {e}")
            if "not connected" in str(e).lower():
                print("  ^^^ Printer disconnected — try removing Windows BT pairing first!")
                return

    wc  = next((c for c in write_chars if c.handle == 0x0020), None) or \
          (write_chars[0] if write_chars else None)
    wc2 = next((c for c in write_chars if c.handle == 0x002A), None)

    if not wc:
        print("No write char — listening 60s...")
        await asyncio.sleep(60)
        return

    async def wr(char, data, label):
        print(f"--> [{label}] h=0x{char.handle:04X} {data.hex()}")
        before = len(responses)
        try:
            await client.write_gatt_char(char.uuid, bytearray(data), response=False)
        except Exception as e:
            print(f"    error: {e}")
        await asyncio.sleep(0.4)
        if len(responses) > before:
            print(f"    ^^^ GOT {len(responses)-before} notification(s)!")

    print("\n--- Handshake ---")
    if wc2:
        await wr(wc2, HANDSHAKE_12A, "hs-12a ch-A"); await wr(wc,  HANDSHAKE_12B, "hs-12b ch-B")
        await asyncio.sleep(0.5)
        await wr(wc2, HANDSHAKE_13A, "hs-13a ch-A"); await wr(wc,  HANDSHAKE_13B, "hs-13b ch-B")
    else:
        await wr(wc, HANDSHAKE_12A, "hs-12a")
        await wr(wc, HANDSHAKE_12B, "hs-12b")
        await asyncio.sleep(0.5)
        await wr(wc, HANDSHAKE_13A, "hs-13a")
        await wr(wc, HANDSHAKE_13B, "hs-13b")

    print("\n--- Status poll (16xx/17xx) ---")
    for suffix in [0x00, 0x01, 0x02]:
        if wc2:
            await wr(wc2, bytes([0x16, suffix]), f"16{suffix:02x} ch-A")
            await wr(wc,  bytes([0x17, suffix]), f"17{suffix:02x} ch-B")
        else:
            await wr(wc, bytes([0x16, suffix]), f"16{suffix:02x}")
            await asyncio.sleep(0.2)
            await wr(wc, bytes([0x17, suffix]), f"17{suffix:02x}")

    print("\n--- Listening 30s ---")
    before = len(responses)
    await asyncio.sleep(30.0)
    print(f"Received {len(responses)-before} packets")
    print(f"\nAll responses ({len(responses)} total):")
    for _, r in responses:
        print(f"  {r}")


    print("    (If pair returns None and printer disconnects, remove the Windows")
    print("     Bluetooth pairing for INSTAX via Settings → Bluetooth & devices)")
    try:
        paired = await client.pair()
        print(f"Pair result: {paired}  (None = already paired, True = fresh pair)")
    except Exception as e:
        print(f"Pair attempt exception: {e}")
    await asyncio.sleep(1.0)

    # Subscribe to all Instax-service notify chars
    instax_notify = [nc for nc in notify_chars if nc.service_uuid != GENERIC_ATTR_SVC]
    for nc in instax_notify:
        try:
            await asyncio.wait_for(client.start_notify(nc.uuid, on_notify), timeout=5.0)
            print(f"Subscribed h=0x{nc.handle:04X}")
        except asyncio.TimeoutError:
            print(f"Subscribe h=0x{nc.handle:04X} timed out")
        except Exception as e:
            print(f"Subscribe h=0x{nc.handle:04X} failed: {e}")
            if "not connected" in str(e).lower():
                print("  ^^^ Printer disconnected — try removing Windows BT pairing first!")
                return

    # Prefer handle 0x0020, else first write char; second write = h=0x002A (channel A)
    wc  = next((c for c in write_chars if c.handle == 0x0020), None) or \
          (write_chars[0] if write_chars else None)
    wc2 = next((c for c in write_chars if c.handle == 0x002A), None)

    if not wc:
        print("No write char — listening 60s anyway...")
        await asyncio.sleep(60)
        return

    print(f"\nWrite char:  h=0x{wc.handle:04X}")
    if wc2:
        print(f"Write char2: h=0x{wc2.handle:04X} (ch-A with DEVICE_ID)")

    async def wr(char, data, label):
        print(f"--> [{label}] h=0x{char.handle:04X} {data.hex()}")
        before = len(responses)
        try:
            await client.write_gatt_char(char.uuid, bytearray(data), response=False)
        except Exception as e:
            print(f"    error: {e}")
        await asyncio.sleep(0.4)
        if len(responses) > before:
            print(f"    ^^^ GOT {len(responses)-before} notification(s)!")

    print("\n--- Handshake (WriteCmd) ---")
    if wc2:
        await wr(wc2, HANDSHAKE_12A, "hs-12a ch-A"); await wr(wc,  HANDSHAKE_12B, "hs-12b ch-B")
        await asyncio.sleep(0.5)
        await wr(wc2, HANDSHAKE_13A, "hs-13a ch-A"); await wr(wc,  HANDSHAKE_13B, "hs-13b ch-B")
    else:
        await wr(wc, HANDSHAKE_12A, "hs-12a"); await asyncio.sleep(0.2)
        await wr(wc, HANDSHAKE_12B, "hs-12b"); await asyncio.sleep(0.5)
        await wr(wc, HANDSHAKE_13A, "hs-13a"); await asyncio.sleep(0.2)
        await wr(wc, HANDSHAKE_13B, "hs-13b")

    print("\n--- Status poll (16xx/17xx, WriteCmd) ---")
    for suffix in [0x00, 0x01, 0x02, 0x03, 0x04]:
        if wc2:
            await wr(wc2, bytes([0x16, suffix]), f"16{suffix:02x} ch-A")
            await wr(wc,  bytes([0x17, suffix]), f"17{suffix:02x} ch-B")
        else:
            await wr(wc, bytes([0x16, suffix]), f"16{suffix:02x}")
            await asyncio.sleep(0.2)
            await wr(wc, bytes([0x17, suffix]), f"17{suffix:02x}")

    print("\n--- Listening 60s ---")
    before = len(responses)
    await asyncio.sleep(60.0)
    print(f"Received {len(responses)-before} packets during listen")
    print(f"\nAll responses ({len(responses)} total):")
    for _, r in responses:
        print(f"  {r}")


# ---------------------------------------------------------------------------
# Scanner + dispatch
# ---------------------------------------------------------------------------

KNOWN_ADDRS = {IOS_ADDR.upper(), ANDROID_ADDR.upper(), NEW_CAM_ADDR.upper()}


async def main():
    responses = []
    found_device = None
    found_event  = asyncio.Event()

    def detection_cb(device, adv):
        nonlocal found_device
        addr = device.address.upper()
        name = (device.name or "").upper()
        if found_device is not None:
            return
        if addr in KNOWN_ADDRS or "INSTAX" in name:
            found_device = device
            found_event.set()

    print("Scanning for any INSTAX device ...")
    print(f"  Known IOS addrs:  {IOS_ADDR}  {NEW_CAM_ADDR}")
    print(f"  Known Android addr: {ANDROID_ADDR}  (will also try Link protocol)")
    print("  Put camera in pairing/connection mode ...")
    async with BleakScanner(detection_callback=detection_cb) as _scanner:
        try:
            await asyncio.wait_for(found_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("Not found in 60s.")
            return

    addr = found_device.address.upper()
    if addr == ANDROID_ADDR.upper():
        profile = "ANDROID (legacy protocol)"
        handler = run_probe_android
    else:
        # All IOS-profile addresses → use Link protocol (javl-compatible)
        profile = "IOS (Link protocol)"
        handler = run_probe_ios

    print(f"Found [{profile}]: {found_device.name!r} @ {found_device.address} — connecting ...")

    disconnected_event = asyncio.Event()

    def on_disconnect(c):
        print("!!! Disconnected from camera !!!")
        disconnected_event.set()

    async with BleakClient(found_device, timeout=30,
                           disconnected_callback=on_disconnect) as client:
        probe_task = asyncio.create_task(handler(client, responses))
        done, _ = await asyncio.wait(
            [probe_task, asyncio.create_task(disconnected_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnected_event.is_set() and not probe_task.done():
            probe_task.cancel()
            print("Probe aborted due to disconnect.")



if __name__ == "__main__":
    asyncio.run(main())
