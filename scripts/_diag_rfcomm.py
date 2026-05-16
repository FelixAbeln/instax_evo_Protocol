"""
Explore Android profile RFCOMM services and attempt a socket connection.
The Android Instax profile at 88:B4:36:11:6F:D2 is Bluetooth Classic using RFCOMM.
"""
import asyncio
import winrt.windows.devices.bluetooth as bt
import winrt.windows.devices.bluetooth.rfcomm as rfcomm

ANDROID_ADDR = 0x88_B4_36_11_6F_D2

async def main():
    print(f"Looking up BluetoothDevice...")
    device = await bt.BluetoothDevice.from_bluetooth_address_async(ANDROID_ADDR)
    if device is None:
        print("Device not found in Windows cache. Put printer in pairing mode first.")
        return

    print(f"Found: {device.name!r}  paired={device.device_information.pairing.is_paired}")

    print("\nEnumerating RFCOMM services...")
    try:
        result = await device.get_rfcomm_services_async()
        print(f"Status: {result.error}  Services: {len(result.services)}")
        for svc in result.services:
            print(f"  Service ID: {svc.service_id.uuid}  ch={svc.connection_host_name}  service={svc.service_name!r}")
    except Exception as e:
        print(f"GetRfcommServices failed: {e}")

    # Try standard SPP UUID
    print("\nLooking for SPP service (0x1101)...")
    try:
        spp_id = rfcomm.RfcommServiceId.from_short_id(0x1101)
        result2 = await device.get_rfcomm_services_for_id_async(spp_id)
        print(f"Status: {result2.error}  Services: {len(result2.services)}")
        for svc in result2.services:
            print(f"  SPP: {svc.service_id.uuid}  host={svc.connection_host_name}  name={svc.service_name!r}")
    except Exception as e:
        print(f"SPP lookup failed: {e}")

asyncio.run(main())
