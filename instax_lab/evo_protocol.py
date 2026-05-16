"""
Instax Link protocol client — IOS BLE profile.

Works for all known camera generations (tested Gen 1 FI019, Gen 2 FI028):
  - Connects to IOS BLE profile (device name ends with "(IOS)")
  - Requires passkey/PIN pairing from the camera's Bluetooth menu before first use
  - Call client.pair() each session to re-establish the encrypted link

Print sequence (javl/InstaxBLE compatible):
  PRINT_IMAGE_DOWNLOAD_START (0x10,0x00): payload = 02 00 00 00 + image_len BE
  × N  PRINT_IMAGE_DOWNLOAD_DATA (0x10,0x01): payload = index BE + chunk (zero-padded)
  PRINT_IMAGE_DOWNLOAD_END   (0x10,0x02): no payload
  PRINT_IMAGE                (0x10,0x80): triggers ejection (only when enable_print=True)
"""

import asyncio
import struct
from io import BytesIO
from math import ceil
from pathlib import Path
from typing import Optional, Union

from bleak import BleakClient, BleakScanner
from PIL import Image

# Instax Link GATT UUIDs (shared across all models and both BLE profiles)
SERVICE_UUID = "70954782-2d83-473d-9e5f-81e1d02d5273"
WRITE_UUID   = "70954783-2d83-473d-9e5f-81e1d02d5273"
NOTIFY_UUID  = "70954784-2d83-473d-9e5f-81e1d02d5273"

# Image dimensions → JPEG chunk size (bytes per PRINT_IMAGE_DOWNLOAD_DATA packet)
FILM_DIMS: dict[tuple[int, int], int] = {
    (600,  800):  900,   # Instax Mini  (FI019 Mini Evo, Mini Link, ...)
    (800,  800): 1808,   # Instax Square
    (1260, 840):  900,   # Instax Wide  (FI028 Evo Wide, Wide Link, ...)
}
MAX_IMAGE_BYTES = 105 * 1024   # 105 KB max JPEG size
BLE_WRITE_CHUNK = 182          # max bytes per BLE write-without-response


# ---------------------------------------------------------------------------
# Packet helpers (module-level so probe scripts can import them)
# ---------------------------------------------------------------------------

def create_packet(op1: int, op2: int, payload: bytes = b'') -> bytes:
    """Build an Instax Link protocol request packet.

    Format: [41 62] [total_len: uint16 BE] [op1] [op2] [payload...] [checksum]
    checksum = (255 - sum(preceding_bytes)) & 255
    """
    header = b'\x41\x62'
    length = struct.pack('>H', 7 + len(payload))
    body   = header + length + bytes([op1, op2]) + payload
    cs     = (255 - (sum(body) & 255)) & 255
    return body + bytes([cs])


def validate_checksum(packet: bytes) -> bool:
    return (sum(packet) & 255) == 255


def decode_response(raw: bytes) -> dict:
    """Decode a Link protocol response notification.

    Returns dict with keys: op (tuple), payload (bytes), error (bool).
    Response payload format for SUPPORT_FUNCTION_INFO and DEVICE_INFO_SERVICE:
      [0x00][InfoType_echo][actual_data...]  — actual data starts at payload[2] = raw[8]
    """
    if len(raw) < 7 or raw[:2] != b'\x61\x42':
        return {"error": True, "raw": raw.hex()}
    if not validate_checksum(raw):
        return {"error": True, "raw": raw.hex(), "reason": "bad checksum"}
    total_len = struct.unpack_from('>H', raw, 2)[0]
    op1, op2  = raw[4], raw[5]
    payload   = raw[6:total_len - 1]
    return {"op": (op1, op2), "payload": payload, "error": False}


# ---------------------------------------------------------------------------
# Camera client
# ---------------------------------------------------------------------------

class InstaxCamera:
    """Instax Link protocol camera client (IOS BLE profile).

    Usage::

        async with InstaxCamera() as cam:
            status = await cam.get_status()
            print(status)
            await cam.print_image("photo.jpg", enable_print=True)
    """

    def __init__(self, address: Optional[str] = None, verbose: bool = False):
        self.address  = address
        self.verbose  = verbose
        self._client: Optional[BleakClient] = None
        self._rx_queue: asyncio.Queue = asyncio.Queue()

        # Populated by get_status()
        self.manufacturer  = ""
        self.model         = ""
        self.serial        = ""
        self.image_size    = (0, 0)   # (width, height) in pixels
        self.battery_state = -1       # 0=critical … 4=full
        self.battery_pct   = -1       # 0–100
        self.photos_left   = -1       # 0–10

    @property
    def chunk_size(self) -> int:
        """JPEG chunk size for PRINT_IMAGE_DOWNLOAD_DATA packets."""
        return FILM_DIMS.get(self.image_size, 900)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        if self.verbose:
            print(f"[instax] {msg}")

    def _notification_handler(self, sender, data: bytearray):
        data = bytes(data)
        self._log(f"  <-- [{len(data)}B] {data.hex()}")
        self._rx_queue.put_nowait(data)

    async def _recv(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)

    async def _send(self, packet: bytes):
        """Send a packet, splitting into ≤182-byte BLE writes."""
        for off in range(0, len(packet), BLE_WRITE_CHUNK):
            await self._client.write_gatt_char(
                WRITE_UUID, bytearray(packet[off:off + BLE_WRITE_CHUNK]), response=False
            )

    async def _send_recv(
        self, op1: int, op2: int, payload: bytes = b'', timeout: float = 5.0
    ) -> dict:
        """Send a Link packet and return the decoded response."""
        pkt = create_packet(op1, op2, payload)
        self._log(f"  --> op=({op1:#04x},{op2:#04x}) payload={payload.hex()!r} [{len(pkt)}B]")
        await self._send(pkt)
        raw = await self._recv(timeout)
        return decode_response(raw)

    # ------------------------------------------------------------------
    # Connection / context manager
    # ------------------------------------------------------------------

    async def connect(self, scan_timeout: float = 30) -> None:
        """Scan, connect, pair, and subscribe to notifications.

        Raises on failure so callers can use try/except.
        """
        self._log("Scanning for INSTAX (IOS) device ...")
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: (
                (self.address and d.address.upper() == self.address.upper())
                or (not self.address
                    and "INSTAX" in (d.name or "").upper()
                    and "(IOS)" in (d.name or "").upper())
            ),
            timeout=scan_timeout,
        )
        if not dev:
            raise RuntimeError("No INSTAX (IOS) device found within timeout")

        self.address = dev.address
        self._log(f"Found {dev.name!r} @ {dev.address}")

        self._client = BleakClient(dev, timeout=30)
        await self._client.connect()
        self._log(f"Connected  MTU={self._client.mtu_size}")

        # Establish encrypted session (required after firmware pairing)
        try:
            await self._client.pair()
            self._log("Paired / encrypted session established")
        except Exception as e:
            self._log(f"pair() exception: {e} — continuing anyway")
        await asyncio.sleep(0.5)

        await self._client.start_notify(NOTIFY_UUID, self._notification_handler)
        self._log("Subscribed to notify char")

    async def disconnect(self):
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        """Read device info, battery, and photos left.

        Populates self.manufacturer, self.model, self.serial,
        self.image_size, self.battery_state, self.battery_pct, self.photos_left.
        Returns a dict with the same values.
        """
        # Hello / init
        await self._send_recv(0x00, 0x00)

        # Device strings: DEVICE_INFO_SERVICE (op=0x00,0x01)
        # Response payload: [0x00][InfoType][str_len][str_bytes...]
        for info_type, attr in [(0, "manufacturer"), (1, "model"), (2, "serial")]:
            dec = await self._send_recv(0x00, 0x01, bytes([info_type]))
            p = dec.get("payload", b"")
            if len(p) >= 4:
                str_len = p[2]
                setattr(self, attr, p[3:3 + str_len].decode("ascii", errors="replace"))

        # Image size: SUPPORT_FUNCTION_INFO (op=0x00,0x02) InfoType=0 (IMAGE_SUPPORT_INFO)
        # Response payload: [0x00][0x00][width: 2B BE][height: 2B BE][...]
        dec = await self._send_recv(0x00, 0x02, b'\x00')
        p = dec.get("payload", b"")
        if len(p) >= 6:
            w, h = struct.unpack_from('>HH', p, 2)
            self.image_size = (w, h)
            if self.image_size not in FILM_DIMS:
                self._log(f"WARNING: Unknown image size {self.image_size}")

        # Battery: SUPPORT_FUNCTION_INFO InfoType=1 (BATTERY_INFO)
        # Response payload: [0x00][0x01][state][pct][...]
        dec = await self._send_recv(0x00, 0x02, b'\x01')
        p = dec.get("payload", b"")
        if len(p) >= 4:
            self.battery_state = p[2]
            self.battery_pct   = p[3]

        # Photos left: SUPPORT_FUNCTION_INFO InfoType=2 (PRINTER_FUNCTION_INFO)
        # Response payload: [0x00][0x02][status_byte][...]
        # photos_left = status_byte & 0x0F, charging = status_byte & 0x80
        dec = await self._send_recv(0x00, 0x02, b'\x02')
        p = dec.get("payload", b"")
        if len(p) >= 3:
            self.photos_left = p[2] & 0x0F

        return {
            "manufacturer":  self.manufacturer,
            "model":         self.model,
            "serial":        self.serial,
            "image_size":    self.image_size,
            "battery_state": self.battery_state,
            "battery_pct":   self.battery_pct,
            "photos_left":   self.photos_left,
        }

    # ------------------------------------------------------------------
    # Image preparation
    # ------------------------------------------------------------------

    def prepare_image(self, source: Union[str, Path, BytesIO]) -> bytearray:
        """Resize and JPEG-encode an image for this camera's film size.

        Requires get_status() to have been called first (to set self.image_size).
        Returns a bytearray of JPEG data ≤ MAX_IMAGE_BYTES.
        """
        if self.image_size == (0, 0):
            raise RuntimeError("Call get_status() first to determine film size")
        w, h = self.image_size

        if isinstance(source, (str, Path)):
            img = Image.open(source)
        elif isinstance(source, BytesIO):
            source.seek(0)
            img = Image.open(source)
        else:
            raise TypeError(f"Unsupported image source: {type(source)}")

        if img.mode == "RGBA":
            img = img.convert("RGB")
        img = img.resize((w, h), Image.Resampling.LANCZOS)

        # Binary-search JPEG quality to stay ≤ MAX_IMAGE_BYTES
        buf = BytesIO()
        lo, hi, quality = 1, 95, 75
        while lo <= hi:
            buf.seek(0); buf.truncate()
            img.save(buf, format="JPEG", quality=quality)
            size = buf.tell()
            if size <= MAX_IMAGE_BYTES and size >= MAX_IMAGE_BYTES * 0.9:
                break
            if size > MAX_IMAGE_BYTES:
                hi = quality - 1
            else:
                lo = quality + 1
            quality = (lo + hi) // 2

        self._log(f"Image prepared: {w}×{h} JPEG quality={quality} size={buf.tell()/1024:.1f}KB")
        return bytearray(buf.getvalue())

    # ------------------------------------------------------------------
    # Print
    # ------------------------------------------------------------------

    async def print_image(
        self,
        source: Union[str, Path, BytesIO],
        enable_print: bool = False,
    ) -> None:
        """Send image to the camera and optionally trigger printing.

        Args:
            source:       Path (str/Path) or BytesIO of the source image.
            enable_print: If True, sends the final PRINT_IMAGE command to
                          physically eject and print. Default False (safe: sends
                          all data packets but does not trigger the print).

        Raises:
            RuntimeError: if no photos are left or get_status() was not called.
        """
        if self.photos_left == 0:
            raise RuntimeError("No photos left in camera — load a new film pack")
        if self.image_size == (0, 0):
            raise RuntimeError("Call get_status() before print_image()")

        img_data   = self.prepare_image(source)
        chunk_size = self.chunk_size
        n_chunks   = ceil(len(img_data) / chunk_size)
        self._log(f"Print: {n_chunks} chunks × {chunk_size}B  total={len(img_data)}B")

        # 1. PRINT_IMAGE_DOWNLOAD_START (0x10, 0x00)
        #    payload: [02 00 00 00] [image_len: 4B BE]
        payload_start = b'\x02\x00\x00\x00' + struct.pack('>I', len(img_data))
        dec = await self._send_recv(0x10, 0x00, payload_start, timeout=10.0)
        if dec.get("error"):
            raise RuntimeError("No response to PRINT_IMAGE_DOWNLOAD_START")

        # 2. PRINT_IMAGE_DOWNLOAD_DATA (0x10, 0x01) × n_chunks
        #    payload: [chunk_index: 4B BE] [chunk: chunk_size B, zero-padded]
        for idx in range(n_chunks):
            chunk = img_data[idx * chunk_size:(idx + 1) * chunk_size]
            chunk = bytes(chunk) + bytes(chunk_size - len(chunk))  # zero-pad
            payload_data = struct.pack('>I', idx) + chunk
            dec = await self._send_recv(0x10, 0x01, payload_data, timeout=10.0)
            if dec.get("error"):
                raise RuntimeError(f"No response to chunk {idx}/{n_chunks}")
            if idx % 10 == 0:
                self._log(f"  chunk {idx + 1}/{n_chunks}")

        # 3. PRINT_IMAGE_DOWNLOAD_END (0x10, 0x02)
        dec = await self._send_recv(0x10, 0x02, timeout=10.0)
        if dec.get("error"):
            raise RuntimeError("No response to PRINT_IMAGE_DOWNLOAD_END")

        # 4. PRINT_IMAGE (0x10, 0x80) — physically ejects and prints
        if enable_print:
            self._log("Sending PRINT_IMAGE — camera will now print!")
            dec = await self._send_recv(0x10, 0x80, timeout=15.0)
            # Refresh photos_left after print
            try:
                dec2 = await self._send_recv(0x00, 0x02, b'\x02', timeout=5.0)
                p = dec2.get("payload", b"")
                if len(p) >= 3:
                    self.photos_left = p[2] & 0x0F
            except asyncio.TimeoutError:
                pass
        else:
            self._log("Print data sent (enable_print=False — not triggering ejection)")


# Backwards-compatible alias (cli.py imports this name)
EvoProtocol = InstaxCamera


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
