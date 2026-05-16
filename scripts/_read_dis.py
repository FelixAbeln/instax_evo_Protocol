"""Read Device Information Service characteristics directly via plain GATT reads."""
import asyncio
from bleak import BleakScanner, BleakClient

KNOWN = {"FA:AB:BC:11:6F:D2", "FA:AB:BC:1D:0A:7B"}

DIS_CHARS = {
    "00002a29-0000-1000-8000-00805f9b34fb": "Manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "Model",
    "00002a25-0000-1000-8000-00805f9b34fb": "Serial",
    "00002a27-0000-1000-8000-00805f9b34fb": "Hardware Rev",
    "00002a26-0000-1000-8000-00805f9b34fb": "Firmware Rev",
    "00002a28-0000-1000-8000-00805f9b34fb": "Software Rev",
}


async def main():
    print("Scanning for INSTAX ...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, a: d.address.upper() in KNOWN or "INSTAX" in (d.name or "").upper(),
        timeout=30,
    )
    if not dev:
        print("Not found.")
        return
    print(f"Found: {dev.name!r} @ {dev.address}")
    async with BleakClient(dev, timeout=20) as c:
        print(f"MTU: {c.mtu_size}")
        for svc in c.services:
            for char in svc.characteristics:
                uuid = str(char.uuid).lower()
                if uuid in DIS_CHARS and "read" in char.properties:
                    try:
                        val = await c.read_gatt_char(char.uuid)
                        text = val.decode("utf-8", errors="replace")
                        print(f"  {DIS_CHARS[uuid]:15s}: {text!r}  raw={val.hex()}")
                    except Exception as e:
                        print(f"  {DIS_CHARS[uuid]:15s}: ERROR {e}")


if __name__ == "__main__":
    asyncio.run(main())
