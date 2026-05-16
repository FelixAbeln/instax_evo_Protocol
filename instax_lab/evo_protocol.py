"""
Instax Evo BLE Protocol Handler

Based on protocol analysis from Android HCI snoop logs.
Service UUIDs: 70954782-2d83-473d-9e5f-81e1d02d5273 (and variants)

Key characteristics:
- 0x0020 (0x1849): Write command channel for image data
- 0x001D (0x1849): Status notifications and device state
- 0x0027 (0x1849): Secondary notify channel
"""

import asyncio
import struct
from typing import Callable, Optional
from bleak import BleakClient, BleakScanner
from rich.console import Console
from rich.table import Table

console = Console()

# Known UUIDs from protocol analysis
SERVICE_UUID = "70954782-2d83-473d-9e5f-81e1d02d5273"
WRITE_CHAR_UUID = "70954783-2d83-473d-9e5f-81e1d02d5273"  # Hypothetical (sequential)
NOTIFY_CHAR_UUID = "70954784-2d83-473d-9e5f-81e1d02d5273"  # Hypothetical (sequential)

# Discovered handle mappings from GATT analysis
HANDLES = {
    "write_command": 0x0020,      # Write channel for image data
    "status_notify": 0x001D,      # Primary status notifications
    "secondary_notify": 0x0027,   # Secondary device notifications
    "tertiary_notify": 0x002A,    # Tertiary channel (rare)
}


class EvoProtocol:
    """Instax Evo BLE Protocol Handler"""

    def __init__(self, device_address: Optional[str] = None, verbose: bool = False):
        self.device_address = device_address
        self.verbose = verbose
        self.client: Optional[BleakClient] = None
        self.device = None
        self.battery_level = 0
        self.image_count = 0
        self.device_state = {}

    def log(self, msg: str):
        """Print debug message if verbose"""
        if self.verbose:
            console.print(f"[bold blue]>>>[/bold blue] {msg}")

    async def scan_for_device(self, timeout: int = 10) -> bool:
        """Scan for Instax Evo device"""
        self.log(f"Scanning for Instax Evo device ({timeout}s)...")
        
        try:
            devices_dict = await BleakScanner.discover(timeout=timeout, return_adv=True)
            devices = [d for d, adv in devices_dict.values()]
            
            instax_devices = [
                d for d in devices
                if d.name and ("INSTAX" in d.name.upper())
            ]
            
            if not instax_devices:
                console.print("[red]No Instax devices found[/red]")
                return False
            
            # Display found devices
            table = Table(title="Found Instax Devices")
            table.add_column("Name", style="cyan")
            table.add_column("Address", style="magenta")
            table.add_column("RSSI", style="green")
            
            for device in instax_devices:
                # Get RSSI from advertisement data
                device_adv = next((adv for d, adv in devices_dict.values() if d.address == device.address), None)
                rssi = device_adv.rssi if device_adv else "N/A"
                table.add_row(
                    device.name or "Unknown",
                    device.address,
                    str(rssi)
                )
            
            console.print(table)
            
            # If specific address provided, use it; otherwise use first device
            if self.device_address:
                self.device = next(
                    (d for d in instax_devices if d.address == self.device_address),
                    None
                )
                if not self.device:
                    console.print(f"[red]Device {self.device_address} not found[/red]")
                    return False
            else:
                self.device = instax_devices[0]
            
            self.log(f"Selected device: {self.device.name} ({self.device.address})")
            self.device_address = self.device.address
            return True
            
        except Exception as e:
            console.print(f"[red]Scan error: {e}[/red]")
            return False

    async def connect(self, timeout: int = 10) -> bool:
        """Connect to device via BLE"""
        if not self.device_address:
            console.print("[red]No device address set[/red]")
            return False

        self.log(f"Connecting to {self.device_address}...")

        try:
            self.client = BleakClient(self.device_address, timeout=timeout)
            await self.client.connect()

            console.print(f"[green]✓ Connected to {self.device_address}[/green]")
            # Iterate services once to trigger WinRT GATT cache population
            _ = list(self.client.services)
            self.log(f"MTU: {self.client.mtu_size}")
            self._status_packets = []
            return True

        except Exception as e:
            console.print(f"[red]Connection failed: {e}[/red]")
            return False

    async def disconnect(self):
        """Disconnect from device"""
        if self.client:
            await self.client.disconnect()
            console.print("[yellow]Disconnected[/yellow]")

    async def discover_services(self) -> bool:
        """Discover GATT services and characteristics"""
        if not self.client or not self.client.is_connected:
            console.print("[red]Not connected[/red]")
            return False
        
        self.log("Discovering GATT database...")
        
        try:
            table = Table(title="GATT Services and Characteristics")
            table.add_column("Service", style="cyan")
            table.add_column("Characteristic", style="magenta")
            table.add_column("UUID", style="yellow")
            table.add_column("Handle", style="green")
            table.add_column("Properties", style="blue")
            
            for service in self.client.services:
                for char in service.characteristics:
                    properties = ", ".join(char.properties)
                    table.add_row(
                        service.uuid[:8] + "...",
                        char.uuid[:8] + "...",
                        char.uuid,
                        str(getattr(char, 'handle', 'N/A')),
                        properties
                    )
            
            console.print(table)
            return True
            
        except Exception as e:
            console.print(f"[red]Discovery error: {e}[/red]")
            return False

    async def subscribe_to_notifications(
        self,
        char_uuid: str,
        callback: Callable[[str, bytearray], None]
    ) -> bool:
        """Subscribe to characteristic notifications"""
        if not self.client or not self.client.is_connected:
            console.print("[red]Not connected[/red]")
            return False
        
        try:
            await self.client.start_notify(char_uuid, callback)
            self.log(f"Subscribed to notifications: {char_uuid[:8]}...")
            return True
            
        except Exception as e:
            console.print(f"[red]Subscription error: {e}[/red]")
            return False

    async def read_characteristic(self, char_uuid: str) -> Optional[bytearray]:
        """Read a characteristic value"""
        if not self.client or not self.client.is_connected:
            console.print("[red]Not connected[/red]")
            return None
        
        try:
            data = await self.client.read_gatt_char(char_uuid)
            self.log(f"Read {char_uuid[:8]}...: {data.hex()}")
            return data
            
        except Exception as e:
            console.print(f"[red]Read error: {e}[/red]")
            return None

    async def write_characteristic(self, char_uuid: str, data: bytes) -> bool:
        """Write to a characteristic"""
        if not self.client or not self.client.is_connected:
            console.print("[red]Not connected[/red]")
            return False
        
        try:
            await self.client.write_gatt_char(char_uuid, data)
            self.log(f"Wrote to {char_uuid[:8]}...: {data.hex()}")
            return True
            
        except Exception as e:
            console.print(f"[red]Write error: {e}[/red]")
            return False

    def parse_status_message(self, data: bytearray) -> dict:
        """Parse device notify packets.

        Known packet formats (from HCI log analysis):
        - 5-byte subtype 01: [type] 01 00 [battery 0-3] [footer]  → battery level
        - 5-byte subtype 02: [type] 02 [img_count] [b3] [b4]       → image queue count
        - 3-byte keepalive:  [msg_id] 00 [seq]                      → keep-alive ping
        - 0xA8 prefix:       A8 [seq] 00 [type] [data...]           → extended status
        """
        if not data:
            return {}

        result = {"raw": data.hex(), "len": len(data)}

        # 0xA8 extended status message
        if data[0] == 0xA8 and len(data) >= 4:
            result["class"] = "status_A8"
            result["sequence"] = data[1]
            result["type"] = f"0x{data[3]:02X}"
            result["payload"] = data[4:].hex() if len(data) > 4 else ""
            return result

        # 5-byte status packets (type 0x16/0x17)
        if len(data) == 5 and data[0] in (0x16, 0x17):
            subtype = data[1]
            if subtype == 0x01:
                result["class"] = "battery"
                result["battery_pips"] = data[3]
                result["battery_pct"] = f"{data[3] * 33}%"
            elif subtype == 0x02:
                result["class"] = "image_count"
                result["images_queued"] = data[2]
            else:
                result["class"] = f"status_5b_sub{subtype:02x}"
            result["channel"] = "0x0027" if data[0] == 0x16 else "0x001D"
            return result

        # 3-byte keep-alive ping (msg_id 0x19/0x1B)
        if len(data) == 3 and data[0] in (0x19, 0x1B) and data[1] == 0x00:
            result["class"] = "keepalive"
            result["seq"] = data[2]
            result["channel"] = "0x0027" if data[0] == 0x19 else "0x001D"
            return result

        result["class"] = "unknown"
        return result

    async def read_device_status(self, timeout: float = 6.0) -> dict:
        """Read image count and battery by sending poll commands and waiting for responses.

        The device responds to 2-byte poll commands 16 01 (battery) and 16 02 (image count)
        sent to the write characteristic. Status packets arrive within ~100ms.
        """
        if not self.client or not self.client.is_connected:
            return {}

        import asyncio

        if not hasattr(self, "_status_packets"):
            self._status_packets = []

        # Find write and notify characteristics
        write_uuid = None
        notify_uuid = None
        for service in self.client.services:
            if "70954782" not in service.uuid:
                continue
            for char in service.characteristics:
                if "70954783" in char.uuid:
                    write_uuid = char.uuid
                if "70954784" in char.uuid and "notify" in char.properties:
                    notify_uuid = char.uuid

        if not notify_uuid:
            return {"all_packets": self._status_packets}

        # Set up fresh collector for this call
        fresh_packets: list = []
        done = asyncio.Event()

        def handler(sender, data: bytearray):
            parsed = self.parse_status_message(data)
            fresh_packets.append(parsed)
            self._status_packets.append(parsed)
            if parsed.get("class") == "battery":
                self.battery_level = parsed["battery_pips"]
            if parsed.get("class") == "image_count":
                self.image_count = parsed["images_queued"]
            battery_seen = any(p.get("class") == "battery" for p in fresh_packets)
            count_seen = any(p.get("class") == "image_count" for p in fresh_packets)
            if battery_seen and count_seen:
                done.set()

        try:
            await self.client.start_notify(notify_uuid, handler)
        except Exception as e:
            self.log(f"Could not subscribe: {e}")
            return {"all_packets": self._status_packets}

        # Send handshake then poll commands (protocol requires handshake before status queries)
        if write_uuid:
            try:
                await asyncio.sleep(0.1)
                # Initial handshake: observed 12-byte and 13-byte writes in HCI log
                # Using zero-padded payloads as probe (device ID bytes vary per session)
                await self.client.write_gatt_char(write_uuid, bytearray([0x00, 0x05] + [0x00]*10), response=False)
                await asyncio.sleep(0.05)
                await self.client.write_gatt_char(write_uuid, bytearray([0x00, 0x00] + [0x00]*11), response=False)
                await asyncio.sleep(0.3)
                # Poll commands: 16 XX requests status type XX
                await self.client.write_gatt_char(write_uuid, bytearray([0x16, 0x00]), response=False)
                await asyncio.sleep(0.05)
                await self.client.write_gatt_char(write_uuid, bytearray([0x16, 0x01]), response=False)
                await asyncio.sleep(0.05)
                await self.client.write_gatt_char(write_uuid, bytearray([0x16, 0x02]), response=False)
                self.log("Sent handshake + poll commands")
            except Exception as e:
                self.log(f"Could not send poll commands: {e}")

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                await self.client.stop_notify(notify_uuid)
            except Exception:
                pass

        battery = next((p["battery_pips"] for p in fresh_packets if p.get("class") == "battery"), None)
        img_count = next((p["images_queued"] for p in fresh_packets if p.get("class") == "image_count"), None)

        result = {"all_packets": fresh_packets}
        if battery is not None:
            result["battery_pips"] = battery
            self.battery_level = battery
        if img_count is not None:
            result["images_queued"] = img_count
            self.image_count = img_count
        return result

    async def read_image_count(self) -> int:
        """Read current image count. Returns -1 on timeout."""
        s = await self.read_device_status()
        return s.get("images_queued", -1)

    async def read_battery(self) -> int:
        """Read battery level (0-3 pips). Returns -1 on timeout."""
        s = await self.read_device_status()
        return s.get("battery_pips", -1)

    async def send_command(self, data: bytes, char_uuid: Optional[str] = None) -> bool:
        """Send a command to the device
        
        Args:
            data: Command bytes to send
            char_uuid: Characteristic UUID (default: write characteristic 70954783...)
        """
        if not self.client or not self.client.is_connected:
            console.print("[red]Not connected[/red]")
            return False
        
        # Use discovered write characteristic if not specified
        if char_uuid is None:
            for service in self.client.services:
                if "70954782" not in service.uuid:
                    continue
                for char in service.characteristics:
                    if "70954783" in char.uuid and ("write" in char.properties):
                        char_uuid = char.uuid
                        break
        
        if not char_uuid:
            console.print("[red]Write characteristic not found[/red]")
            return False
        
        try:
            await self.client.write_gatt_char(char_uuid, data)
            console.print(f"[green]→[/green] Sent {len(data)} bytes: [cyan]{data.hex()}[/cyan]")
            return True
        except Exception as e:
            console.print(f"[red]Write failed: {e}[/red]")
            return False

    async def setup_notifications(self) -> bool:
        """Subscribe to all notify characteristics and log incoming data"""
        if not self.client or not self.client.is_connected:
            console.print("[red]Not connected[/red]")
            return False
        
        notify_chars = []
        
        for service in self.client.services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    notify_chars.append((char.uuid, service.uuid))
        
        if not notify_chars:
            console.print("[yellow]No notify characteristics found[/yellow]")
            return False
        
        console.print(f"[cyan]Setting up {len(notify_chars)} notification subscriptions...[/cyan]")
        
        for char_uuid, service_uuid in notify_chars:
            def make_handler(uuid: str):
                def handler(sender, data: bytearray):
                    parsed = self.parse_status_message(data)
                    if parsed:
                        console.print(f"[magenta]←[/magenta] Status: {parsed}")
                    else:
                        console.print(f"[magenta]←[/magenta] Notify from {uuid[:8]}...: [yellow]{data.hex()}[/yellow]")
                return handler
            
            try:
                handler = make_handler(char_uuid)
                await self.client.start_notify(char_uuid, handler)
                console.print(f"  [green]✓[/green] Subscribed: {char_uuid[:8]}... (service {service_uuid[:8]}...)")
            except Exception as e:
                console.print(f"  [yellow]⚠[/yellow] Could not subscribe {char_uuid[:8]}...: {e}")
        
        return True
