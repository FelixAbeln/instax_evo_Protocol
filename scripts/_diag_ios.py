import asyncio
from bleak import BleakScanner, BleakClient

ADDR = "FA:AB:BC:11:6F:D2"
WRITE_UUID  = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"
DEVICE_ID = bytes([0x8d, 0x3d, 0xb0, 0xe5, 0x92, 0x59, 0x03, 0x3d])
HS12 = bytes([0x00, 0x05]) + DEVICE_ID + bytes([0x00, 0x00])
HS13 = bytes([0x00, 0x00]) + DEVICE_ID + bytes([0x04, 0x00, 0x00])

async def main():
    print("Scanning...")
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=30)
    if dev is None:
        print("Not found"); return

    responses = []
    def on_notify(sender, data):
        print(f"  <-- NOTIFY: {data.hex()}")
        responses.append(data.hex())

    async with BleakClient(dev) as client:
        print(f"MTU={client.mtu_size}")

        # Read standard chars to confirm link
        try:
            name = await client.read_gatt_char("00002a00-0000-1000-8000-00805f9b34fb")
            print(f"Device name: {bytes(name)}")
        except Exception as e:
            print(f"Read name failed: {e}")

        await client.start_notify(NOTIFY_UUID, on_notify)
        print("Subscribed to notify char")

        # Handshake with device-specific bytes (WNWR)
        for lbl, data in [("hs12-device", HS12), ("hs13-device", HS13)]:
            print(f"WNWR {lbl} {data.hex()}")
            await client.write_gatt_char(WRITE_UUID, bytearray(data), response=False)
            await asyncio.sleep(0.3)

        # Poll: try both WR and WNWR for each command
        for suffix in [0x00, 0x01, 0x02]:
            for rsp in [True, False]:
                cmd = bytes([0x16, suffix])
                lbl = "WR  " if rsp else "WNWR"
                print(f"{lbl} {cmd.hex()}")
                try:
                    await client.write_gatt_char(WRITE_UUID, bytearray(cmd), response=rsp)
                except Exception as e:
                    print(f"  err: {e}")
                await asyncio.sleep(0.3)

        print("Listening 30s for any spontaneous notifications...")
        await asyncio.sleep(30)
        print(f"Total notifications received: {len(responses)}")

asyncio.run(main())
