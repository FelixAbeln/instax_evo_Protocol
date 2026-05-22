"""
Instax Link protocol client â€” Link BLE profile.

Works for all known camera generations (tested Gen 1 FI019, Gen 2 FI028):
    - Connects to the Link BLE profile on `FA:AB:BC:*`
    - Gen 1 may require a one-time OS-level passkey/PIN pairing step on Windows
    - Current maintained flow does not rely on calling client.pair() each session

Print sequence (javl/InstaxBLE compatible):
  PRINT_IMAGE_DOWNLOAD_START (0x10,0x00): payload = 02 00 00 00 + image_len BE
  Ã— N  PRINT_IMAGE_DOWNLOAD_DATA (0x10,0x01): payload = index BE + chunk (zero-padded)
  PRINT_IMAGE_DOWNLOAD_END   (0x10,0x02): no payload
  PRINT_IMAGE                (0x10,0x80): triggers ejection (only when enable_print=True)
"""

import asyncio
import struct
import time
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

# Image dimensions â†’ JPEG chunk size (bytes per PRINT_IMAGE_DOWNLOAD_DATA packet)
# Keys are (width, height) as reported by IMAGE_SUPPORT_INFO (InfoType 0x00).
# Always query the camera first â€” never assume dimensions from the model name.
FILM_DIMS: dict[tuple[int, int], int] = {
    (600,  800):  900,   # Instax Mini portrait  (FI019 Mini Evo, Mini Link, ...)
    (800,  600):  900,   # Instax Mini landscape  (Gen 3 Cinema â€” smartphone print mode)
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
      [0x00][InfoType_echo][actual_data...]  â€” actual data starts at payload[2] = raw[8]
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
    """Instax Link protocol camera client.

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
        self.battery_state = -1       # 0=critical â€¦ 4=full
        self.battery_pct   = -1       # 0â€“100
        self.photos_left   = -1       # 0â€“10

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
        """Send a packet, splitting into â‰¤182-byte BLE writes."""
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

    async def _recv_match(
        self,
        op1: int,
        op2: int,
        timeout: float = 5.0,
        payload_predicate=None,
    ) -> dict:
        """Receive until the expected response opcode (and optional payload predicate) matches."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise asyncio.TimeoutError(f"timeout waiting for ({op1:#04x},{op2:#04x})")
            raw = await self._recv(timeout=left)
            dec = decode_response(raw)
            if dec.get("error"):
                continue
            rop1, rop2 = dec.get("op", (-1, -1))
            if (rop1, rop2) != (op1, op2):
                continue
            if payload_predicate is not None and not payload_predicate(dec.get("payload", b"")):
                continue
            return dec

    async def _send_recv_match(
        self,
        op1: int,
        op2: int,
        payload: bytes = b"",
        timeout: float = 5.0,
        payload_predicate=None,
    ) -> dict:
        pkt = create_packet(op1, op2, payload)
        self._log(f"  --> op=({op1:#04x},{op2:#04x}) payload={payload.hex()!r} [{len(pkt)}B]")
        await self._send(pkt)
        return await self._recv_match(
            op1=op1,
            op2=op2,
            timeout=timeout,
            payload_predicate=payload_predicate,
        )

    # ------------------------------------------------------------------
    # Connection / context manager
    # ------------------------------------------------------------------

    async def connect(self, scan_timeout: float = 30) -> None:
        """Scan, connect, and subscribe to notifications.

        Raises on failure so callers can use try/except.
        """
        self._log("Scanning for INSTAX Link device ...")
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: (
                (self.address and d.address.upper() == self.address.upper())
                or (not self.address
                    and "INSTAX" in (d.name or "").upper()
                    and ("(IOS)" in (d.name or "").upper()
                         or "(BLE)" in (d.name or "").upper()))
            ),
            timeout=scan_timeout,
        )
        if not dev:
            raise RuntimeError("No INSTAX Link device found within timeout")

        self.address = dev.address
        self._log(f"Found {dev.name!r} @ {dev.address}")

        # Use the address string rather than the scanned BLEDevice object so a
        # reconnect gets a fresh GATT resolution instead of reusing stale cache.
        self._client = BleakClient(self.address, timeout=30)
        await self._client.connect()
        self._log(f"Connected  MTU={self._client.mtu_size}")

        # Let WinRT finish service resolution before subscribing. The current
        # maintained flow relies on the existing OS bond and avoids explicit
        # per-session pair() calls here.
        get_services = getattr(self._client, "get_services", None)
        if callable(get_services):
            await get_services()
        await asyncio.sleep(1.0)

        for attempt in range(1, 4):
            try:
                await self._client.start_notify(
                    NOTIFY_UUID, self._notification_handler
                )
                break
            except Exception as e:
                if attempt == 3:
                    raise
                self._log(f"start_notify retry {attempt}/3: {e}")
                await asyncio.sleep(2.0)

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

        # Evo-specific session registers (required for live HIST tracking).
        # Without these the camera does NOT log shots taken while BLE is
        # connected, so the image-total counter never advances.
        # See docs/session-init.md.
        try:
            await self._send_recv(0x20, 0x10, timeout=3.0)            # FW_PROGRAM_INFO
        except asyncio.TimeoutError:
            self._log("warn: no reply to (0x20,0x10) FW_PROGRAM_INFO")
        try:
            await self._send_recv(0x80, 0x10, b'\x00', timeout=3.0)   # Evo session reg
        except asyncio.TimeoutError:
            self._log("warn: no reply to (0x80,0x10) session register")

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
        Returns a bytearray of JPEG data â‰¤ MAX_IMAGE_BYTES.
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

        # Binary-search JPEG quality to stay â‰¤ MAX_IMAGE_BYTES
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

        self._log(f"Image prepared: {w}Ã—{h} JPEG quality={quality} size={buf.tell()/1024:.1f}KB")
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
            raise RuntimeError("No photos left in camera â€” load a new film pack")
        if self.image_size == (0, 0):
            raise RuntimeError("Call get_status() before print_image()")

        img_data   = self.prepare_image(source)
        chunk_size = self.chunk_size
        n_chunks   = ceil(len(img_data) / chunk_size)
        self._log(f"Print: {n_chunks} chunks Ã— {chunk_size}B  total={len(img_data)}B")

        # 1. PRINT_IMAGE_DOWNLOAD_START (0x10, 0x00)
        #    payload: [02 00 00 00] [image_len: 4B BE]
        payload_start = b'\x02\x00\x00\x00' + struct.pack('>I', len(img_data))
        dec = await self._send_recv(0x10, 0x00, payload_start, timeout=10.0)
        if dec.get("error"):
            raise RuntimeError("No response to PRINT_IMAGE_DOWNLOAD_START")

        # 2. PRINT_IMAGE_DOWNLOAD_DATA (0x10, 0x01) Ã— n_chunks
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

        # 4. PRINT_IMAGE (0x10, 0x80) â€” physically ejects and prints
        if enable_print:
            self._log("Sending PRINT_IMAGE â€” camera will now print!")
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
            self._log("Print data sent (enable_print=False â€” not triggering ejection)")

    # ------------------------------------------------------------------
    # Print history (0x82 / 0x84 family â€” confirmed 2026-05-17)
    # ------------------------------------------------------------------

    async def get_history_count(self) -> int:
        """Return the number of images stored in the camera's print history.

        Sends HISTORY_ENTRY_QUERY (0x84, 0x09) with index=0.
        Camera response payload (14 bytes):
          [2B index_echo][4B size_field][4B size_field][4B entry_count BE]
        Returns entry_count (0 if response is malformed or empty).
        """
        dec = await self._send_recv(0x84, 0x09, b'\x00', timeout=5.0)
        p = dec.get("payload", b"")
        # 14-byte response â†’ valid entry list; 1-byte 0x80 â†’ no history
        if len(p) >= 14:
            return struct.unpack_from(">I", p, 10)[0]
        return 0

    async def download_history_image(self, index: int = 0) -> bytes:
        """Download a JPEG stored in the camera's print history.

        Protocol flow (confirmed from BLE capture 19-51-52):
          1. HISTORY_ENTRY_QUERY   (0x84,0x09) with [index]
          2. HISTORY_ENTRY_SUBQUERY (0x84,0x0a) with [index][00 00 00 00]
          3. HISTORY_ENTRY_ACK     (0x84,0x0b) with [index]
          4. HISTORY_DOWNLOAD_PREPARE (0x80,0x15) with 17 zero bytes
          5. HISTORY_DOWNLOAD_START   (0x82,0x00) with [index]
          6. Loop: HISTORY_DOWNLOAD_DATA (0x82,0x01) pull (empty payload)
               â†’ camera replies with framed [2B chunk_idx][JPEG...] notification
               â†’ followed by N raw (unframed) ATT notifications (JPEG continuation)
             Repeat until JPEG EOI (ff d9) detected or no data for 2 s.
          7. HISTORY_DOWNLOAD_END (0x82,0x02) with [0x00]

        Args:
            index: 0-based history entry index.

        Returns:
            Raw JPEG bytes for the requested history entry.

        Raises:
            RuntimeError: on protocol errors or if no image data is received.
        """
        # ---- 1-3: Entry query / ack ----
        # Wide Evo: returns 14 bytes (all zeros). Mini Evo: returns 1 byte (0x80).
        # Neither model puts useful data in the response; proceed regardless.
        meta = await self._send_recv(0x84, 0x09, bytes([index]), timeout=5.0)
        mp = meta.get("payload", b"")
        self._log(f"  0x84,0x09 response [{len(mp)}B]: {mp.hex() or 'empty'}")
        await self._send_recv(0x84, 0x0a, bytes([index]) + bytes(4), timeout=5.0)
        await self._send_recv(0x84, 0x0b, bytes([index]), timeout=5.0)

        # ---- 4: Prepare ----
        await self._send_recv(0x80, 0x15, bytes(17), timeout=5.0)

        # ---- 5: Start ----
        dec = await self._send_recv(0x82, 0x00, bytes([index]), timeout=10.0)
        if dec.get("error"):
            raise RuntimeError(f"HISTORY_DOWNLOAD_START rejected for index {index}")

        # ---- 6: Pull loop ----
        pull_pkt  = create_packet(0x82, 0x01)   # empty payload pull request
        jpeg_buf  = bytearray()
        MAX_PULLS = 1000   # safety cap; typical Evo Wide image needs ~176 pulls

        for pull_num in range(MAX_PULLS):
            self._log(f"  pull {pull_num + 1}")
            await self._send(pull_pkt)

            # Drain all notifications for this pull.
            # Use 5s timeout for first notification (camera takes ~200ms), then
            # 0.5s drain timeout for continuation fragments within same burst.
            got_any = False
            transfer_ended = False
            first_in_pull = True
            while True:
                timeout = 5.0 if first_in_pull else 0.50
                try:
                    raw = await asyncio.wait_for(
                        self._rx_queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    break   # end of this pull's burst

                first_in_pull = False
                got_any = True
                if raw[:2] == b'\x61\x42':
                    dec2 = decode_response(raw)
                    if dec2.get("error"):
                        continue
                    op1, op2 = dec2["op"]
                    if op1 == 0x82 and op2 == 0x01:
                        p = dec2["payload"]
                        if len(p) <= 2:
                            # Camera signalled end of data (no JPEG chunk)
                            transfer_ended = True
                            break
                        # Skip 2-byte chunk index, take JPEG payload
                        jpeg_buf.extend(p[2:])
                    elif op1 == 0x82 and op2 == 0x02:
                        # Camera signalled end of transfer
                        transfer_ended = True
                        break
                    # Any other framed notification: ignore
                else:
                    # Raw ATT notification â€” bare JPEG continuation bytes
                    jpeg_buf.extend(raw)

            # Check for JPEG End-Of-Image marker
            eoi_pos = jpeg_buf.find(b'\xff\xd9')
            if eoi_pos != -1:
                jpeg_buf = jpeg_buf[:eoi_pos + 2]
                self._log(f"  JPEG complete after pull {pull_num + 1} "
                          f"({len(jpeg_buf) / 1024:.1f} KB)")
                break

            if transfer_ended or not got_any:
                break

        # ---- 7: End ----
        await self._send_recv(0x82, 0x02, b'\x00', timeout=5.0)

        if not jpeg_buf:
            raise RuntimeError(
                f"No image data received for history index {index}"
            )
        if jpeg_buf[:2] != b'\xff\xd8':
            self._log(f"WARNING: downloaded data does not start with JPEG SOI "
                      f"(starts with {jpeg_buf[:4].hex()})")

        return bytes(jpeg_buf)

    # ------------------------------------------------------------------
    # Favorites slots (0x80,0x17 + 0x85 save bracket)
    # ------------------------------------------------------------------

    async def favorites_read_slot(self, slot: int, selector: int = 1, timeout: float = 6.0) -> bytes:
        """Read one favorites slot surface.

        selector=1 -> slot content/title surface
        selector=2 -> slot state surface
        """
        if not 1 <= slot <= 10:
            raise ValueError("slot must be in range 1..10")
        if selector not in (1, 2):
            raise ValueError("selector must be 1 or 2")

        req = bytes([selector, 0x00, slot, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

        def pred(p: bytes) -> bool:
            return len(p) >= 3 and p[1] == selector and p[2] == slot

        dec = await self._send_recv_match(
            0x80,
            0x17,
            req,
            timeout=timeout,
            payload_predicate=pred,
        )
        return dec.get("payload", b"")

    async def favorites_dump_slots(self, max_slot: int = 10, timeout: float = 6.0) -> list[dict]:
        if max_slot < 1:
            raise ValueError("max_slot must be >= 1")
        rows: list[dict] = []
        for slot in range(1, max_slot + 1):
            sel1 = await self.favorites_read_slot(slot=slot, selector=1, timeout=timeout)
            sel2 = await self.favorites_read_slot(slot=slot, selector=2, timeout=timeout)
            row = {
                "slot": slot,
                "selector_01": sel1.hex(),
                "selector_02": sel2.hex(),
                "occupied_01": sel1[3] if len(sel1) >= 4 else None,
                "occupied_02": sel2[3] if len(sel2) >= 4 else None,
            }
            rows.append(row)
        return rows

    async def favorites_write_slot(
        self,
        slot: int,
        profile_blob: bytes,
        title: str,
        state_blob: bytes,
        timeout: float = 8.0,
    ) -> dict:
        """Write one favorites slot using the confirmed 0x85-bracketed two-write flow."""
        if not 1 <= slot <= 10:
            raise ValueError("slot must be in range 1..10")
        if len(profile_blob) != 8:
            raise ValueError("profile_blob must be exactly 8 bytes")
        title_b = title.encode("ascii")
        if len(title_b) != 3:
            raise ValueError("title must be exactly 3 ASCII characters")
        if len(state_blob) != 11:
            raise ValueError("state_blob must be exactly 11 bytes")

        write_a = bytes([0x01, 0x02, slot, 0x00]) + profile_blob + title_b
        write_b = bytes([0x02, 0x02, slot, 0x00]) + state_blob

        pre_8500 = await self._send_recv_match(0x85, 0x00, b"", timeout=timeout)
        pre_8501 = await self._send_recv_match(0x85, 0x01, bytes.fromhex("070001000000000000"), timeout=timeout)

        ack_a = await self._send_recv_match(
            0x80,
            0x17,
            write_a,
            timeout=timeout,
            payload_predicate=lambda p: len(p) >= 3 and p[1] == 0x01 and p[2] == slot,
        )
        ack_b = await self._send_recv_match(
            0x80,
            0x17,
            write_b,
            timeout=timeout,
            payload_predicate=lambda p: len(p) >= 3 and p[1] == 0x02 and p[2] == slot,
        )

        post_8500 = await self._send_recv_match(0x85, 0x00, b"", timeout=timeout)
        post_8501 = await self._send_recv_match(0x85, 0x01, bytes.fromhex("070000000000000000"), timeout=timeout)

        return {
            "slot": slot,
            "write_a": write_a.hex(),
            "write_b": write_b.hex(),
            "ack_8500_pre": pre_8500.get("payload", b"").hex(),
            "ack_8501_pre": pre_8501.get("payload", b"").hex(),
            "ack_a": ack_a.get("payload", b"").hex(),
            "ack_b": ack_b.get("payload", b"").hex(),
            "ack_8500_post": post_8500.get("payload", b"").hex(),
            "ack_8501_post": post_8501.get("payload", b"").hex(),
        }

    async def favorites_write_default_slot(self, slot: int, title: str = "DEF", timeout: float = 8.0) -> dict:
        """Write the currently validated full-default favorites profile to a slot."""
        profile_blob = bytes.fromhex("0000000032000000")
        state_blob = bytes.fromhex("0000000000000000000000")
        return await self.favorites_write_slot(
            slot=slot,
            profile_blob=profile_blob,
            title=title,
            state_blob=state_blob,
            timeout=timeout,
        )


# Backwards-compatible alias (cli.py imports this name)
EvoProtocol = InstaxCamera

