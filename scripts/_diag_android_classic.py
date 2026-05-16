"""
Try to access the Android BT Classic profile's GATT services via WinRT BluetoothDevice.
The printer's Android profile at 88:B4:36:11:6F:D2 is Bluetooth Classic (BR/EDR),
but it exposes GATT services over the classic L2CAP ATT fixed channel.
"""
import asyncio
import winrt.windows.devices.bluetooth as bt
import winrt.windows.devices.bluetooth.genericattributeprofile as gatt

ANDROID_ADDR = 0x88_B4_36_11_6F_D2   # classic BT address

async def main():
    print(f"Looking up BluetoothDevice for 0x{ANDROID_ADDR:012X}...")
    device = await bt.BluetoothDevice.from_bluetooth_address_async(ANDROID_ADDR)
    if device is None:
        print("BluetoothDevice returned None — device not in Windows classic BT cache")
        print("Try pairing it first via 'Add a device' in Windows Bluetooth settings.")
        return

    print(f"Found: name={device.name!r}  class_of_device={device.class_of_device}")
    print(f"  Connection status: {device.connection_status}")
    print(f"  Is paired: {device.device_information.pairing.is_paired}")

    print("\nEnumerating GATT services over BR/EDR...")
    try:
        result = await device.get_gatt_services_async()
        print(f"GATT result status: {result.status}")  # 0=Success
        services = result.services
        print(f"Services found: {len(services)}")
        for svc in services:
            print(f"  Service: {svc.uuid}  session={svc.session}")
            try:
                chars_result = await svc.get_characteristics_async()
                for ch in chars_result.characteristics:
                    props = int(ch.characteristic_properties)
                    print(f"    Char: {ch.uuid}  handle={ch.attribute_handle:#06x}  props={props:#010b}")
            except Exception as e:
                print(f"    GetCharacteristics failed: {e}")
    except Exception as e:
        print(f"GetGattServices failed: {e}")

asyncio.run(main())
