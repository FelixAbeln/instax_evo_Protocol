"""
Probe the Instax Evo device to find command that triggers status notifications.

Key insight from Android HCI log analysis:
  - Write characteristic ATT handle: 0x0020 (32 decimal) — Android confirmed
  - Notify char 1 ATT handle: 0x001D (29)  — CCCD at 0x001E (30)
  - Notify char 2 ATT handle: 0x0027 (39)  — CCCD at 0x0028 (40)
  - Windows BT GATT cache may show WRONG handles — bypass with UUID writes

Handshake sequence from old HCI log (T=0.575s after connect):
  1. Write 12b to h=0x002A: 0005 <device_id_8b> 0000
  2. Write 12b to h=0x0020: 0005 0100000000000000 0000
  3. Write 13b to h=0x002A: 0000 <device_id_8b> 040000
  4. Write 13b to h=0x0020: 0000 0100000000000000 040000
  5. Write 2b  to h=0x002A: 1600
  6. Write 2b  to h=0x0020: 1700  → device responds: notify 1600d6b77b1b (6b)
  7. Write 2b  to h=0x002A: 1601
  8. → device responds: 1601000344 (battery=3, FULL)
  9. Write 2b  to h=0x0020: 1701
 10. Write 2b  to h=0x002A: 1602
 11. → device responds: 1602010202 (image_count=1)
"""
import asyncio
from bleak import BleakScanner, BleakClient

# Shared Instax GATT service UUIDs (same across all known models)
WRITE_UUID = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"

# --- OLD camera (Instax Mini Evo / first gen) ---
# Android dual-channel protocol: write to h=0x0020/0x002A, notify from h=0x001D/0x0027
# Device-specific bytes from HCI log (embedded in channel-A writes)
DEVICE_ID     = bytes([0x8d, 0x3d, 0xb0, 0xe5, 0x92, 0x59, 0x03, 0x3d])
HANDSHAKE_12A = bytes([0x00, 0x05]) + DEVICE_ID + bytes([0x00, 0x00])         # ch-A 12b
HANDSHAKE_13A = bytes([0x00, 0x00]) + DEVICE_ID + bytes([0x04, 0x00, 0x00])    # ch-A 13b
HANDSHAKE_12B = bytes([0x00, 0x05, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])  # ch-B
HANDSHAKE_13B = bytes([0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04, 0x00, 0x00])

# --- NEW camera (FI028 / second gen) ---
# Single-channel TLV protocol: write to h=0x0010, notify from h=0x0012, CCCD at h=0x0013
# Packet format: [41 62 00] [total_len 1B] [cmd 1B] [args...] [checksum 1B]
# Checksum = (~sum_of_all_preceding_bytes) & 0xFF
def _new_cam_pkt(*args):
    """Build a new-camera request packet with correct length and checksum."""
    body = bytes(args)  # cmd, arg, arg, ...
    length = 4 + len(body) + 1  # header(3) + len(1) + body + checksum(1)
    raw = b'\x41\x62\x00' + bytes([length]) + body
    checksum = (~sum(raw)) & 0xFF
    return raw + bytes([checksum])

NEW_CAM_HELLO   = _new_cam_pkt(0x00)          # cmd=0x00 init
NEW_CAM_STATUS  = _new_cam_pkt(0x02, 0x00)    # cmd=0x02 status (battery, film, etc.)


ANDROID_ADDR  = "E0:48:24:D7:CF:2E"   # OLD camera – Android BLE profile (confirmed from HCI log)
IOS_ADDR      = "FA:AB:BC:11:6F:D2"   # OLD camera – IOS BLE profile (random MAC)
NEW_CAM_ADDR  = "FA:AB:BC:1D:0A:7B"   # NEW camera – FI028 (from 19-51-52 bugreport HCI log)

GENERIC_ATTR_SVC = "00001801-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# New-camera (FI028) protocol handler
# ---------------------------------------------------------------------------

def _decode_new_status(data: bytes) -> dict:
    """
    Parse cmd=0x02 response body (after status byte).
    Observed: 04 ec03 4802 0b00 0a50 00 01 000000
      byte[0] = battery_level (0–4, 4=full)
      bytes[5:7] = LE uint16 remaining film shots
    """
    result = {}
    if len(data) >= 1:
        result["battery_level"] = data[0]       # 4 = full, guessed
    if len(data) >= 7:
        result["film_remaining"] = data[5] | (data[6] << 8)
    return result


async def run_probe_new_cam(client: "BleakClient", responses: list):
    """Protocol handler for the new FI028-generation camera."""
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
        hex_data = data.hex()
        tag = f"0x{sender:04X}" if isinstance(sender, int) else str(sender)
        print(f"  <-- NOTIFY {tag}: [{len(data)}] {hex_data}")
        responses.append((sender, data))

    # New camera: write char at h=0x0010, notify at h=0x0012
    wc = next((c for c in write_chars if c.handle == 0x0010), None) or \
         next((c for c in write_chars), None)
    nc = next((c for c in notify_chars if c.handle == 0x0012), None) or \
         next((c for c in notify_chars if c.service_uuid != GENERIC_ATTR_SVC), None)

    if not wc or not nc:
        print("Could not find write/notify chars — dumping all write chars:")
        for c in write_chars:
            print(f"  write h=0x{c.handle:04X}")
        return

    print(f"\nWrite char:  h=0x{wc.handle:04X}")
    print(f"Notify char: h=0x{nc.handle:04X}")

    # Subscribe to notify
    try:
        await asyncio.wait_for(client.start_notify(nc.uuid, on_notify), timeout=5.0)
        print(f"Subscribed h=0x{nc.handle:04X}")
    except Exception as e:
        print(f"Subscribe failed: {e}")

    async def wr(pkt, label):
        print(f"--> [{label}] {pkt.hex()}")
        before = len(responses)
        try:
            await client.write_gatt_char(wc.uuid, bytearray(pkt), response=False)
        except Exception as e:
            print(f"    error: {e}")
        await asyncio.sleep(0.5)
        if len(responses) > before:
            print(f"    ^^^ GOT {len(responses)-before} notification(s)!")

    print("\n--- New-cam init sequence ---")
    await wr(NEW_CAM_HELLO,  "hello/init cmd=0x00")
    await wr(_new_cam_pkt(0x01, 0x00), "manufacturer  cmd=0x01 sub=0x00")
    await wr(_new_cam_pkt(0x01, 0x01), "model         cmd=0x01 sub=0x01")

    print("\n--- New-cam status poll ---")
    await wr(NEW_CAM_STATUS, "status cmd=0x02")

    print(f"\n--- Listening 30s ---")
    before = len(responses)
    await asyncio.sleep(30.0)
    print(f"Received {len(responses)-before} extra packets")

    print(f"\nAll responses ({len(responses)} total):")
    for sender, raw in responses:
        hex_data = raw.hex() if isinstance(raw, (bytes, bytearray)) else raw
        # Try to decode cmd=0x02 status
        if len(raw) >= 8 and raw[:3] == b'\x61\x42\x00' and raw[4] == 0x02:
            data_after_status = raw[6:-1]  # skip header, length, cmd, status-byte, checksum
            parsed = _decode_new_status(data_after_status)
            print(f"  STATUS: {parsed}  raw={hex_data}")
        else:
            print(f"  {hex_data}")


# ---------------------------------------------------------------------------
# Old-camera (Mini Evo / first gen) protocol handler
# ---------------------------------------------------------------------------

async def run_probe_old_cam(client: "BleakClient", responses: list):
    """Protocol handler for the original Mini Evo camera."""
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
        hex_data = data.hex()
        tag = f"0x{sender:04X}" if isinstance(sender, int) else str(sender)
        print(f"  <-- NOTIFY {tag}: [{len(data)}] {hex_data}")
        # Decode known responses
        if data[:2] == b'\x16\x01' and len(data) >= 5:
            batt = data[4]
            lvl  = {3: "HIGH", 2: "MED", 1: "LOW", 0: "CRIT"}.get(batt, f"?({batt})")
            print(f"      *** BATTERY = {batt} ({lvl})")
        elif data[:2] == b'\x16\x02' and len(data) >= 5:
            count = data[3] | (data[4] << 8)
            print(f"      *** IMAGE COUNT = {count}")
        responses.append((sender, hex_data))

    # Pair — NOTE: if this returns None the bond is stale → printer may disconnect.
    # Fix: remove INSTAX from Windows Bluetooth settings and re-run for a fresh pair.
    print("\n--- Pairing / bonding ---")
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
    print(f"  Known addrs: IOS={IOS_ADDR}  ANDROID={ANDROID_ADDR}  NEW={NEW_CAM_ADDR}")
    print("  Put printer in pairing/connection mode (press Bluetooth button) ...")
    async with BleakScanner(detection_callback=detection_cb) as _scanner:
        try:
            await asyncio.wait_for(found_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            print("Not found in 60s.")
            return

    addr = found_device.address.upper()
    if addr == NEW_CAM_ADDR.upper():
        profile = "NEW (FI028)"
        handler = run_probe_new_cam
    elif addr == ANDROID_ADDR.upper():
        profile = "OLD (Android)"
        handler = run_probe_old_cam
    else:
        profile = "OLD (IOS/unknown)"
        handler = run_probe_old_cam

    print(f"Found [{profile}]: {found_device.name!r} @ {found_device.address} — connecting ...")

    disconnected_event = asyncio.Event()

    def on_disconnect(c):
        print("!!! Disconnected from printer !!!")
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
