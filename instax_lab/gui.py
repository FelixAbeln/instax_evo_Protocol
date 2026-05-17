"""
Instax BLE Camera Monitor — main GUI application.

Main window:
  - Camera info panel (model, serial, battery, photos left)
  - Console log panel (mirrors all terminal output)
  - Toolbar: Connect / Disconnect / Live View / Print Image

Transfer:
  - Polls CAMERA_FUNCTION_INFO automatically once connected
  - Camera path is auto-selected from the model ID at connect time:
      FI028 (Evo Wide)  → uses (88,xx) image pull  [confirmed]
      FI019 (Mini Evo)  → image pull unsupported;  live view + print work
      unknown model     → falls back to FI028-like behaviour
  - Each camera model's behaviour lives in instax_lab/cameras/<model>.py

Live View window:
  - Opens on demand; uses (82,xx) frame pull protocol
  - Confirmed on both FI019 and FI028

Usage
-----
    python -m instax_lab [BLE_ADDRESS]
    python -m instax_lab.gui [BLE_ADDRESS]

    Default address: FA:AB:BC:1D:0A:7B  (Instax Evo Wide)
    Mini Evo:        FA:AB:BC:11:6F:D2
"""

from __future__ import annotations

import asyncio
import queue
from collections.abc import Callable
import struct
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk
from bleak import BleakClient, BleakScanner

from .protocol import make_packet, NOTIFY_UUID, WRITE_UUID, DEFAULT_ADDR
from .cameras import get_path
from .cameras.base import BaseCameraPath

# ── colour palette ─────────────────────────────────────────────────────────────
BG   = "#1e1e1e"
BG2  = "#252526"
BG3  = "#2d2d30"
FG   = "#d4d4d4"
DIM  = "#777777"
OK   = "#4ec9b0"
WARN = "#dcdcaa"
ERR  = "#f44747"
ACC  = "#0e639c"


# ══════════════════════════════════════════════════════════════════════════════
# Async camera backend  (runs in a daemon thread with its own event loop)
# ══════════════════════════════════════════════════════════════════════════════

class CameraBackend:
    """All BLE I/O lives here.  Communicates with the GUI via two queues:
      ui_q   — backend → GUI  (dicts with a 'kind' key)
      cmd_q  — GUI → backend  (dicts with a 'cmd' key)

    Camera-model-specific behaviour (image pull, feature flags) is
    delegated to ``self._path`` which is selected automatically after
    reading the model ID at connect time.
    """

    def __init__(self, ui_q: queue.Queue, address: str):
        self.ui_q    = ui_q
        self.address = address

        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: BleakClient | None = None
        # Queues created inside the daemon thread so they bind to the correct
        # event loop (required on Python < 3.10).
        self._rx: asyncio.Queue | None = None
        self._cmd_q: asyncio.Queue | None = None

        self._connected  = False
        self._liveview   = False
        self._lv_running = False   # True while _liveview_loop task is executing
        self._ble_busy   = False   # True while a (88,xx) or (10,xx) op is running
        self._stop       = False
        # Runtime flag: set False after a (88,xx) timeout so we stop retrying.
        # Also set False on connect for FI019 (path.supports_image_pull=False).
        self._transfer_supported = True
        self._img_w = 0   # populated from IMAGE_SUPPORT_INFO on connect
        self._img_h = 0

        # Camera path: selects model-specific behaviour after model detection.
        # Defaults to the base (FI028-like) path until the model is known.
        self._path: BaseCameraPath = BaseCameraPath()

    # ── thread entry ──────────────────────────────────────────────────────────

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._run, daemon=True, name="BLE")
        t.start()
        return t

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._rx         = asyncio.Queue()
        self._cmd_q      = asyncio.Queue()
        self._ble_op_lock = asyncio.Lock()   # serialises per-frame BLE exchanges
        self._loop.run_until_complete(self._main())

    # ── thread-safe command sender (called from GUI thread) ───────────────────

    def send_cmd(self, cmd: str, **kw):
        if self._loop and self._cmd_q:
            self._loop.call_soon_threadsafe(
                self._cmd_q.put_nowait, {"cmd": cmd, **kw}
            )

    # ── UI event emitter ──────────────────────────────────────────────────────

    def _ui(self, kind: str, **kw):
        self.ui_q.put_nowait({"kind": kind, **kw})

    def _log(self, text: str):
        print(text)
        self._ui("log", text=text)

    # ── BLE notify handler ────────────────────────────────────────────────────

    def _on_notify(self, _sender, data: bytearray):
        self._rx.put_nowait(bytes(data))

    # ── frame assembler ───────────────────────────────────────────────────────

    async def _recv_frame(self, timeout: float = 5.0):
        """Accumulate ATT notifications until a complete IOS-Link frame arrives.

        Handles BLE fragmentation: a single IOS-Link frame may span several
        ATT notifications (e.g. 5 notifications for a 1027-byte (82,01) frame
        at bonded MTU=247).

        Returns (op1, op2, payload_bytes).
        """
        buf = bytearray()
        deadline = self._loop.time() + timeout
        while True:
            left = deadline - self._loop.time()
            if left <= 0:
                raise asyncio.TimeoutError()
            raw = await asyncio.wait_for(self._rx.get(), timeout=left)
            buf.extend(raw)
            if len(buf) < 6:
                continue
            if buf[:2] != b"\x61\x42":
                buf.clear()
                continue
            total = struct.unpack_from(">H", buf, 2)[0]
            if len(buf) >= total:
                frame = bytes(buf[:total])
                del buf[:total]
                return frame[4], frame[5], frame[6:total - 1]

    async def _write(self, pkt: bytes):
        """Write a packet, splitting into ≤182-byte BLE writes (required for
        large payloads such as PRINT_IMAGE_DOWNLOAD_DATA at ~911 bytes)."""
        if self._client:
            for off in range(0, len(pkt), 182):
                await self._client.write_gatt_char(
                    WRITE_UUID, pkt[off:off + 182], response=False
                )

    # ── rx flush ──────────────────────────────────────────────────────────────

    async def _flush_rx(self):
        """Discard stale notifications before sending a new command."""
        while True:
            try:
                self._rx.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ── main async dispatcher ─────────────────────────────────────────────────

    async def _main(self):
        while not self._stop:
            try:
                msg = await asyncio.wait_for(self._cmd_q.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            cmd = msg.get("cmd")
            if cmd == "connect":
                asyncio.create_task(self._connect())
            elif cmd == "disconnect":
                asyncio.create_task(self._disconnect())
            elif cmd == "liveview_start":
                asyncio.create_task(self._liveview_loop())
            elif cmd == "liveview_stop":
                self._liveview = False
            elif cmd == "set_flash":
                asyncio.create_task(self._set_flash(msg["value"]))
            elif cmd == "download_photo":
                asyncio.create_task(self._download_photo())
            elif cmd == "print_image":
                asyncio.create_task(
                    self._print_image(msg["path"], msg.get("enable_print", False))
                )
            elif cmd == "scan":
                asyncio.create_task(self._scan())
            elif cmd == "set_address":
                self.address = msg["address"]
                self._log(f"Camera address → {self.address}")
            elif cmd == "stop":
                self._stop = True

    # ── connection ────────────────────────────────────────────────────────────

    async def _connect(self):
        self._ui("status", state="connecting")
        self._log(f"Scanning for {self.address} ...")

        for attempt in range(5):
            try:
                dev = await BleakScanner.find_device_by_address(
                    self.address, timeout=15.0
                )
                if not dev:
                    self._log(
                        f"  Not found (attempt {attempt + 1}/5) — retrying in 3 s"
                    )
                    await asyncio.sleep(3.0)
                    continue

                # Use address string (not device object) — forces fresh GATT
                # service discovery; avoids "Characteristic not found" on Windows
                # after a camera-initiated disconnect.
                self._client = BleakClient(self.address, timeout=30)
                await self._client.connect()
                mtu = self._client.mtu_size
                self._log(f"Connected  MTU={mtu}")

                # MTU=23 means Windows reconnected before the BLE stack fully
                # negotiated — the GATT cache may be stale.  Bail out and wait
                # for the stack to settle before retrying.
                if mtu <= 23:
                    raise RuntimeError(
                        f"MTU={mtu} — BLE stack not ready yet, will retry"
                    )

                # MTU=247 means Windows already has bond keys and the session is
                # encrypted — do NOT call pair() here.  Calling pair() on an
                # already-bonded Wide Evo triggers a full re-pairing handshake
                # that the camera rejects by dropping the connection immediately.
                # The camera must be paired once via Windows Bluetooth settings;
                # after that the WinRT stack re-uses the cached bond keys
                # automatically and no explicit pair() call is needed.

                # Brief settle for WinRT GATT cache to populate.
                await asyncio.sleep(1.0)

                # If the camera dropped the link, fail fast instead of burning
                # 3× GATT retries at 2 s each.
                if not self._client.is_connected:
                    raise RuntimeError("Camera disconnected before subscribe")

                # Retry start_notify up to 3× in case GATT isn't ready yet.
                for disc in range(3):
                    try:
                        await self._client.start_notify(
                            NOTIFY_UUID, self._on_notify
                        )
                        break
                    except Exception as e:
                        if disc == 2:
                            raise
                        self._log(
                            f"  GATT not ready (try {disc+1}/3): {e}"
                            " — retrying in 2 s"
                        )
                        await asyncio.sleep(2.0)

                self._log("Subscribed to notifications")
                self._connected = True
                break

            except asyncio.CancelledError:
                # Event loop is shutting down — clean up quietly and exit.
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                return
            except Exception as e:
                self._log(f"  Attempt {attempt + 1} failed: {e}")
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                # Longer pause on low-MTU retry so the BLE stack can settle.
                # Also wait longer when the camera dropped the link after pair().
                delay = 5.0 if ("MTU=" in str(e) or "after pair" in str(e)) else 3.0
                await asyncio.sleep(delay)

        if not self._connected:
            self._log("Could not connect after 5 attempts")
            self._ui("status", state="error", msg="Could not connect")
            return

        await self._read_status()
        self._ui("status", state="connected")
        asyncio.create_task(self._poll_loop())

    async def _disconnect(self):
        self._connected = False
        self._liveview  = False
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._ui("status", state="disconnected")
        self._log("Disconnected")

    # ── BLE scanner ───────────────────────────────────────────────────────────

    async def _scan(self):
        """Discover nearby BLE devices and emit scan_done with the list."""
        self._ui("scan_start")
        self._log("Scanning for BLE devices (8 s) …")
        try:
            devices = await BleakScanner.discover(timeout=8.0)
            results = [
                {
                    "address": d.address.upper(),
                    "name":    d.name or "",
                    "rssi":    getattr(d, "rssi", None),
                }
                for d in sorted(
                    devices,
                    key=lambda x: getattr(x, "rssi", None) or -999,
                    reverse=True,
                )
            ]
            self._log(f"Scan complete — {len(results)} device(s) found")
            self._ui("scan_done", devices=results)
        except Exception as e:
            self._log(f"Scan error: {e}")
            self._ui("scan_error", msg=str(e))

    # ── camera status ─────────────────────────────────────────────────────────

    async def _read_status(self):
        info: dict = {}

        # Hello
        try:
            await self._write(make_packet(0x00, 0x00))
            await asyncio.wait_for(self._rx.get(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        # Device strings (manufacturer / model / serial)
        for info_type, key in [(0, "manufacturer"), (1, "model"), (2, "serial")]:
            try:
                await self._write(make_packet(0x00, 0x01, bytes([info_type])))
                _, _, p = await self._recv_frame(timeout=3.0)
                if len(p) >= 4:
                    info[key] = p[3: 3 + p[2]].decode("ascii", errors="replace")
            except asyncio.TimeoutError:
                pass

        # Select camera path based on model ID.
        # This must happen before we emit camera_info so the UI can show the
        # path's display_name alongside the raw model string.
        model_id    = info.get("model", "")
        self._path  = get_path(model_id)
        # Initialise the runtime flag from the path's static capability.
        self._transfer_supported = self._path.supports_image_pull

        # Image dimensions (determines film format and chunk size)
        try:
            await self._write(make_packet(0x00, 0x02, b"\x00"))
            _, _, p = await self._recv_frame(timeout=3.0)
            if len(p) >= 6:
                w, h = struct.unpack_from(">HH", p, 2)
                self._img_w = w
                self._img_h = h
                info["img_w"] = w
                info["img_h"] = h
        except asyncio.TimeoutError:
            pass

        # Battery
        try:
            await self._write(make_packet(0x00, 0x02, b"\x01"))
            _, _, p = await self._recv_frame(timeout=3.0)
            if len(p) >= 4:
                info["battery_state"] = p[2]
                info["battery_pct"]   = p[3]
        except asyncio.TimeoutError:
            pass

        # Photos left
        try:
            await self._write(make_packet(0x00, 0x02, b"\x02"))
            _, _, p = await self._recv_frame(timeout=3.0)
            if len(p) >= 3:
                info["photos_left"] = p[2] & 0x0F
        except asyncio.TimeoutError:
            pass

        self._ui("camera_info", **info)
        bat = info.get("battery_pct", "?")
        pht = info.get("photos_left", "?")
        self._log(
            f"Camera: {self._path.display_name}"
            f"  battery={bat}%  photos_left={pht}"
        )
        if not self._path.supports_image_pull:
            self._log(
                f"  ↳ Image pull (88,xx) disabled for {self._path.display_name}"
            )

    # ── transfer polling loop ─────────────────────────────────────────────────

    async def _poll_loop(self):
        self._log(
            "\nPolling for transfers — press Share on camera to pull images ..."
        )
        await asyncio.sleep(1.5)
        pulled = 0

        _pull_unsupported_logged = False   # suppress repeated "not available" spam

        while self._connected:
            # Never poll while live view or a print is active — both use the
            # same BLE notification queue and will corrupt each other's reads.
            if self._liveview or self._ble_busy:
                await asyncio.sleep(1.0)
                continue

            if not self._client or not self._client.is_connected:
                self._log("Connection lost — will retry")
                self._connected = False
                self._ui("status", state="disconnected")
                await asyncio.sleep(5.0)
                asyncio.create_task(self._connect())
                return

            # Flush stale notifications before the poll command so _recv_frame
            # can't pick up a leftover response from a prior operation.
            await self._flush_rx()

            try:
                await self._write(make_packet(0x00, 0x02, b"\x04"))
            except Exception as e:
                self._log(f"Write error: {e}")
                break

            try:
                _, _, p = await self._recv_frame(timeout=3.0)
            except asyncio.TimeoutError:
                await asyncio.sleep(1.0)
                continue

            flag = p[4] if len(p) > 4 else 0

            if flag != 0x00:
                # Respect both the path's static capability flag and the
                # runtime flag (set False after a timeout on an unknown model).
                if not self._path.supports_image_pull or not self._transfer_supported:
                    if not _pull_unsupported_logged:
                        self._log(
                            f"Transfer flag=0x{flag:02x} — image pull not available"
                            f" on {self._path.display_name}"
                        )
                        _pull_unsupported_logged = True
                    await asyncio.sleep(1.0)
                    continue

                self._log(f"Transfer ready (flag=0x{flag:02x})")
                self._ui("transfer_start")
                self._ble_busy = True
                try:
                    ok = await self._path.pull_one(self)
                finally:
                    self._ble_busy = False

                if ok:
                    pulled += 1
                    self._log(f"[{pulled} image(s) pulled — checking for more]")
                    await asyncio.sleep(1.5)
                    continue
                else:
                    await asyncio.sleep(3.0)
                    continue

            await asyncio.sleep(1.0)

    # ── image preparation ─────────────────────────────────────────────────────

    def _prepare_image(self, path: str) -> bytes:
        """Resize *path* to the camera's film dimensions and JPEG-encode it.

        Uses letterbox scaling (no cropping).  Binary-searches JPEG quality
        to stay within the camera's buffer limit (proportionally scaled from
        the known Mini Evo 105 KB cap).
        """
        w, h = self._img_w, self._img_h
        if w == 0 or h == 0:
            raise RuntimeError(
                "Image dimensions unknown — connect to camera first"
            )

        img = Image.open(path).convert("RGB")
        img.thumbnail((w, h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        off_x = (w - img.width)  // 2
        off_y = (h - img.height) // 2
        canvas.paste(img, (off_x, off_y))

        max_bytes = int(105 * 1024 * w * h / (600 * 800))

        lo, hi, quality = 1, 95, 80
        buf = None
        for _ in range(14):
            from io import BytesIO as _BIO
            buf = _BIO()
            canvas.save(buf, format="JPEG", quality=quality)
            size = buf.tell()
            if size <= max_bytes and size >= max_bytes * 0.90:
                break
            if size > max_bytes:
                hi = quality - 1
            else:
                lo = quality + 1
            quality = (lo + hi) // 2

        self._log(
            f"Image prepared: {w}×{h}  quality={quality}"
            f"  size={buf.tell() / 1024:.1f} KB"
        )
        return buf.getvalue()

    # ── print (10,xx) ─────────────────────────────────────────────────────────

    async def _print_image(self, path: str, enable_print: bool):
        """Send a JPEG to the camera and optionally trigger the physical print.

        Protocol:
          (10,00) DOWNLOAD_START  — payload: 02 00 00 00 + img_size BE
          (10,01) DOWNLOAD_DATA   — payload: seq BE + 900B chunk (zero-padded)
          (10,02) DOWNLOAD_END    — no payload
          (10,80) PRINT_IMAGE     — only when enable_print=True; ejects film
        """
        CHUNK_SIZE = 900
        self._ble_busy = True
        try:
            self._log(f"Print: preparing {Path(path).name} ...")
            img_data = self._prepare_image(path)
            n_chunks = (len(img_data) + CHUNK_SIZE - 1) // CHUNK_SIZE
            self._log(f"Print: {len(img_data):,} B in {n_chunks} chunks")
            self._ui("print_start", total_chunks=n_chunks, size=len(img_data))

            await self._flush_rx()

            # (10,00) START ───────────────────────────────────────────────────
            start_payload = b"\x02\x00\x00\x00" + struct.pack(">I", len(img_data))
            await self._write(make_packet(0x10, 0x00, start_payload))
            _, _, ack = await self._recv_frame(timeout=10.0)
            self._log(f"  START ack  {ack.hex()}")

            # (10,01) DATA × N ────────────────────────────────────────────────
            for idx in range(n_chunks):
                chunk = img_data[idx * CHUNK_SIZE:(idx + 1) * CHUNK_SIZE]
                chunk = bytes(chunk) + bytes(CHUNK_SIZE - len(chunk))  # zero-pad
                await self._write(
                    make_packet(0x10, 0x01, struct.pack(">I", idx) + chunk)
                )
                _, _, _ = await self._recv_frame(timeout=10.0)
                self._ui("print_chunk", chunk=idx, total_chunks=n_chunks)
                if idx % 20 == 0 or idx == n_chunks - 1:
                    self._log(f"  chunk {idx + 1}/{n_chunks}")

            # (10,02) END ─────────────────────────────────────────────────────
            await self._write(make_packet(0x10, 0x02))
            _, _, ack = await self._recv_frame(timeout=10.0)
            self._log(f"  END ack  {ack.hex() if ack else '(empty)'}")

            # (10,80) PRINT ───────────────────────────────────────────────────
            if enable_print:
                self._log("  PRINT — ejecting film ...")
                await self._write(make_packet(0x10, 0x80))
                _, _, ack = await self._recv_frame(timeout=15.0)
                self._log(f"  PRINT ack  {ack.hex() if ack else '(empty)'}")
                try:
                    await self._flush_rx()
                    await self._write(make_packet(0x00, 0x02, b"\x02"))
                    _, _, p = await self._recv_frame(timeout=3.0)
                    if len(p) >= 3:
                        self._ui("camera_info", photos_left=p[2] & 0x0F)
                except Exception:
                    pass

            self._log(
                f"Print {'complete — film ejected' if enable_print else 'data sent (no ejection)'}"
            )
            self._ui("print_done", printed=enable_print)

        except asyncio.TimeoutError:
            msg = "Timeout during print — camera may not have responded"
            self._log(f"  Print error: {msg}")
            self._ui("print_error", msg=msg)
        except Exception as e:
            self._log(f"  Print error: {e}")
            self._ui("print_error", msg=str(e))
        finally:
            self._ble_busy = False

    # ── live view (82,xx) ─────────────────────────────────────────────────────

    async def _liveview_loop(self):
        """Pull live frames via (82,xx).

        Open the session with (82,00), then repeatedly send (82,01) and receive
        the response using _recv_frame, which reassembles all ATT continuation
        notifications into one complete frame automatically.

        Each (82,01) response payload layout (confirmed from btsnoop capture):
          payload[0:2] = chunk index (always 0x0001)
          payload[2:5] = 3-byte header field
          payload[5:]  = complete JPEG (SOI … EOI)

        Session management:
          - After each session ends (timeout or camera-close), the session is
            automatically reopened so live view runs continuously until the user
            clicks Stop.
          - If the camera returns 0 frames (session rejected / busy), we retry
            up to 3 consecutive times with a 2 s delay.  After 3 failures the
            loop stops and the user must click Start again.
          - A 50 ms post-frame drain catches spontaneous (82,02) notifications
            so they don't pollute the next _recv_frame call.
        """
        while self._ble_busy:
            await asyncio.sleep(0.1)
        self._liveview   = True
        self._lv_running = True
        self._log("Live view: starting ...")
        pull_pkt = make_packet(0x82, 0x01)

        total_frames      = 0
        consecutive_empty = 0   # sessions that delivered 0 frames in a row

        try:
            while self._liveview and self._connected:
                # ── open session ──────────────────────────────────────────────
                await self._flush_rx()
                await self._write(make_packet(0x82, 0x00, b"\x00"))
                try:
                    op1, op2, _ = await self._recv_frame(timeout=3.0)
                    if not (op1 == 0x82 and op2 == 0x00):
                        self._log(
                            "Live view: unexpected response to (82,00) — stopping"
                        )
                        break
                except asyncio.TimeoutError:
                    self._log(
                        "Live view: no ack for (82,00) — camera may not support live view"
                    )
                    break

                frame_count = 0
                session_done = False

                # ── continuous pull ───────────────────────────────────────────
                while self._liveview and self._connected and not session_done:
                    async with self._ble_op_lock:
                        await self._write(pull_pkt)

                        try:
                            op1, op2, payload = await self._recv_frame(timeout=5.0)
                        except asyncio.TimeoutError:
                            # Camera stopped responding — close and reopen the session.
                            session_done = True
                            break

                    if op1 == 0x82 and op2 == 0x01:
                        if len(payload) <= 5:
                            # Camera returned an empty frame — session rejected.
                            session_done = True
                            break
                        # JPEG at payload[5:] — skip 2B chunk_idx + 3B header
                        jpeg_data = payload[5:]
                        soi = jpeg_data.find(b"\xff\xd8")
                        eoi = jpeg_data.rfind(b"\xff\xd9")
                        if soi >= 0 and eoi > soi:
                            frame_count  += 1
                            total_frames += 1
                            frame = jpeg_data[soi:eoi + 2]
                            self._log(
                                f"Live view: frame {total_frames}"
                                f" ({len(frame)/1024:.1f} KB)"
                            )
                            self._ui("liveview_frame", data=frame)

                    elif op1 == 0x82 and op2 == 0x02:
                        if frame_count > 0 and self._liveview and self._connected:
                            # Camera fired the shutter and terminated the pull
                            # stream.  Acknowledge, download the photo, then
                            # reopen the pull stream — all without leaving this
                            # inner loop so the LV session looks seamless.
                            self._log(
                                f"Live view: shutter fired"
                                f" ({frame_count} frame(s)) — downloading …"
                            )
                            await self._write(make_packet(0x82, 0x02, b"\x00"))
                            try:
                                await self._check_82_transfer()
                            except Exception as e:
                                self._log(f"Auto-transfer error: {e}")
                            # Give the camera time to recover before new LV session.
                            await asyncio.sleep(2.0)
                            await self._flush_rx()
                            await self._write(make_packet(0x82, 0x00, b"\x00"))
                            try:
                                rp1, rp2, _ = await self._recv_frame(timeout=3.0)
                            except asyncio.TimeoutError:
                                rp1, rp2 = None, None
                            if rp1 == 0x82 and rp2 == 0x00 and self._liveview:
                                frame_count = 0
                                consecutive_empty = 0
                                continue  # resume pulling frames
                            # Reopen failed — let the outer loop retry.
                            frame_count = 0  # prevent outer loop re-transfer
                            session_done = True
                        else:
                            self._log("Live view: camera closed session")
                            session_done = True
                        break

                    # Instant non-blocking drain: check if (82,02) has already
                    # arrived in the queue before sending the next pull.
                    # A (82,02) close frame is 7 bytes — one BLE notification.
                    # If it's there we catch it with zero delay; if not we
                    # proceed immediately (any late (82,02) will be consumed by
                    # the next _recv_frame call and handled there).
                    try:
                        raw = self._rx.get_nowait()
                        if (len(raw) >= 6
                                and raw[0:2] == b"\x61\x42"
                                and raw[4] == 0x82
                                and raw[5] == 0x02):
                            self._log("Live view: camera closed session (post-frame)")
                            session_done = True
                        else:
                            # Unexpected data — keep it for the next recv
                            self._rx.put_nowait(raw)
                    except asyncio.QueueEmpty:
                        pass

                # ── close session ─────────────────────────────────────────────
                await self._write(make_packet(0x82, 0x02, b"\x00"))
                try:
                    await self._recv_frame(timeout=2.0)
                except asyncio.TimeoutError:
                    pass

                if frame_count == 0:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        self._log(
                            "Live view: camera refused 3 sessions in a row — stopping"
                        )
                        break
                    self._log(
                        f"Live view: no frames received"
                        f" (attempt {consecutive_empty}/3) — retrying in 2 s …"
                    )
                    await asyncio.sleep(2.0)
                else:
                    consecutive_empty = 0
                    self._log(
                        f"Live view: session ended after {frame_count} frame(s)"
                        f"  [{total_frames} total]"
                    )
                    # Check for auto-transferred image (remote shutter during live view).
                    # Camera needs ~4-5 s to encode; _check_82_transfer polls with timeout.
                    if self._liveview and self._connected:
                        try:
                            await self._check_82_transfer()
                        except Exception as e:
                            self._log(f"Auto-transfer error: {e}")
                    # Reopen immediately — no artificial gap between sessions.

        except Exception as e:
            self._log(f"Live view error: {e}")

        self._liveview   = False
        self._lv_running = False
        self._log(f"Live view stopped  ({total_frames} frame(s) total)")

    async def _check_82_transfer(self):
        """Check for and receive an auto-transferred image via (82,10/20/21/22).

        Called after a live view session that delivered frames.  The camera
        takes ~4-5 s to encode the photo after the shutter fires, so we poll
        (82,20) with a 500 ms interval until it reports READY or 30 s elapse.

        Protocol (confirmed from btsnoop capture):
          phone→cam (82,10) 00                      IMG_HIST_QUERY
          cam→phone (82,10) 00                      ACK
          phone→cam (82,20)                         IMG_HIST_POLL (repeat ~500 ms)
          cam→phone (82,20) [02]                    not ready
          cam→phone (82,20) [00 02 total:4B chunk_sz:4B]  READY
          phone→cam (82,21) [idx:4B BE]             request chunk N
          cam→phone (82,21) [status:1B][idx:4B BE][jpeg…]  chunk N data
          … (next request is the implicit ACK) …
          phone→cam (82,22)                         IMG_HIST_END
          cam→phone (82,22) [00]                    done
        """
        # Block the poll loop so its (0x00,0x02) responses don't land in our
        # receive queue while we're waiting for (82,21) chunk frames.
        self._ble_busy = True
        try:
            await self._flush_rx()

            # Step 1: query
            await self._write(make_packet(0x82, 0x10, b"\x00"))
            try:
                o1, o2, _ = await self._recv_frame(timeout=3.0)
            except asyncio.TimeoutError:
                self._log("Auto-transfer: no response to IMG_HIST_QUERY — skipping")
                return
            if not (o1 == 0x82 and o2 == 0x10):
                self._log("Auto-transfer: unexpected response to IMG_HIST_QUERY — skipping")
                return

            # Step 2: poll until ready (max 60 × 500 ms = 30 s)
            total_size = 0
            chunk_size = 0
            ready = False
            for attempt in range(60):
                await self._write(make_packet(0x82, 0x20))
                try:
                    o1, o2, p = await self._recv_frame(timeout=3.0)
                except asyncio.TimeoutError:
                    self._log("Auto-transfer: poll timeout — giving up")
                    return
                if not (o1 == 0x82 and o2 == 0x20):
                    self._log("Auto-transfer: unexpected opcode during poll — skipping")
                    return
                if len(p) >= 10:
                    # READY payload: [status][0x02][total:4B BE][chunk_sz:4B BE]
                    total_size = struct.unpack_from(">I", p, 2)[0]
                    chunk_size = struct.unpack_from(">I", p, 6)[0]
                    ready = True
                    self._log(
                        f"Auto-transfer: READY  {total_size} B"
                        f"  chunk={chunk_size} B"
                        f"  ~{-(-total_size // chunk_size)} chunks"
                    )
                    break
                # not ready — [0x02] single byte response
                await asyncio.sleep(0.5)

            if not ready:
                self._log("Auto-transfer: image not ready after 30 s — skipping")
                return

            # Step 3: request each chunk.
            # P→C: (82,21)[idx:4B]   C→P: (82,21)[status:1B][idx:4B][data…]
            # The next request serves as the implicit ACK for the previous chunk.
            num_chunks = -(-total_size // chunk_size)  # ceiling division
            self._ui("transfer_start")
            self._ui(
                "transfer_meta",
                total=total_size,
                chunks=num_chunks,
                timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            jpeg = bytearray()
            transfer_ok = True
            for chunk_idx in range(num_chunks):
                await self._write(make_packet(0x82, 0x21, struct.pack(">I", chunk_idx)))
                # Skip any unsolicited frames that arrive before the chunk response.
                got_chunk = False
                for _ in range(10):
                    try:
                        o1, o2, cp = await self._recv_frame(timeout=10.0)
                    except asyncio.TimeoutError:
                        self._log("Auto-transfer: chunk timeout — transfer incomplete")
                        transfer_ok = False
                        break
                    if o1 == 0x82 and o2 == 0x21:
                        # [1B status][4B idx echo][JPEG data…]
                        if len(cp) >= 5:
                            jpeg.extend(cp[5:])
                        got_chunk = True
                        break
                    self._log(
                        f"Auto-transfer: skipping unsolicited frame"
                        f" ({o1:#04x},{o2:#04x})"
                    )
                if not got_chunk:
                    transfer_ok = False
                    break
                self._ui("transfer_progress", chunk=chunk_idx, total_chunks=num_chunks)

            # Step 4: close the transfer session
            await self._write(make_packet(0x82, 0x22))
            try:
                await self._recv_frame(timeout=2.0)
            except asyncio.TimeoutError:
                pass

            if len(jpeg) > 100:
                self._log(f"Auto-transfer complete: {len(jpeg)} B received")
                soi = bytes(jpeg).find(b"\xff\xd8")
                if soi < 0:
                    self._log("Auto-transfer: no JPEG SOI — discarding")
                else:
                    jpeg_bytes = bytes(jpeg[soi:])
                    out_dir = Path("captures/image_transfer")
                    out_dir.mkdir(parents=True, exist_ok=True)
                    ts = time.strftime("%Y-%m-%d_%H%M%S")
                    out = out_dir / f"autotransfer_{ts}.jpg"
                    out.write_bytes(jpeg_bytes)
                    self._log(f"Saved {len(jpeg_bytes):,} B → {out}")
                    self._ui(
                        "transfer_done",
                        path=str(out),
                        size=len(jpeg_bytes),
                        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            else:
                self._log("Auto-transfer: too few bytes — discarded")
        finally:
            self._ble_busy = False

    async def _set_flash(self, value: int):
        """Send SET_INFO flash mode.  value: 0=AUTO, 1=ON, 2=OFF."""
        if not self._connected:
            return
        async with self._ble_op_lock:
            await self._flush_rx()
            payload = bytes([0x0b, 0x02, value, 0x00, 0x00, 0x00])
            await self._write(make_packet(0x80, 0x11, payload))
            try:
                await self._recv_frame(timeout=2.0)
                name = {0: "AUTO", 1: "ON", 2: "OFF"}.get(value, str(value))
                self._log(f"Flash: {name}")
            except asyncio.TimeoutError:
                self._log("Flash set: no ACK")

    async def _download_photo(self):
        """Trigger an immediate photo download via the (82,10/20/21/22) protocol."""
        if not self._connected:
            return
        lv_was_active = self._lv_running
        # Signal liveview loop to stop, then wait for it to fully exit so the
        # RX queue is clean before we send IMG_HIST_QUERY.
        self._liveview = False
        wait_s = 0.0
        while self._lv_running and wait_s < 8.0:
            await asyncio.sleep(0.1)
            wait_s += 0.1
        if self._lv_running:
            self._log("Download photo: live view didn't stop — proceeding anyway")
        try:
            await self._check_82_transfer()
        except Exception as e:
            self._log(f"Download photo error: {e}")
        # Resume live view if it was active when Download Photo was clicked.
        if lv_was_active and self._connected:
            self._liveview = True
            asyncio.create_task(self._liveview_loop())


# ══════════════════════════════════════════════════════════════════════════════
# Camera selection helpers
# ══════════════════════════════════════════════════════════════════════════════

def _guess_model(name: str) -> str:
    """Guess the Instax model from a BLE advertisement name."""
    upper = name.upper()
    for mid in ("FI028", "FI019", "FI027", "FI030"):
        if mid in upper:
            return mid
    if "MINI" in upper and "EVO" in upper:
        return "~FI019 (Mini Evo)"
    if "WIDE" in upper and "EVO" in upper:
        return "~FI028 (Evo Wide)"
    if "INSTAX" in upper:
        return "Instax"
    return ""


class ScanDialog:
    """Modal BLE scanner window.

    Opens, immediately kicks off a scan, populates a Treeview with results.
    Calls ``on_connect(address)`` when the user picks a camera and clicks
    Connect (or double-clicks a row).  Results are delivered via the normal
    ui_q path: InstaxApp._handle routes scan_done / scan_error here.
    """

    def __init__(
        self,
        parent: tk.Misc,
        backend: "CameraBackend",
        on_connect: Callable[[str], None],
    ):
        self._backend    = backend
        self._on_connect = on_connect
        self._all_devices: list[dict] = []

        self.win = tk.Toplevel(parent)
        self.win.title("Select Camera")
        self.win.geometry("600x420")
        self.win.configure(bg=BG)
        self.win.resizable(True, True)
        self.win.grab_set()

        self._build()
        # Auto-scan on open.
        self.win.after(100, self._scan)

    def _build(self):
        ttk.Label(
            self.win,
            text=(
                "Scanning for nearby Bluetooth cameras.\n"
                "Select one and click Connect (or double-click a row)."
            ),
            wraplength=560, justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=12, pady=(10, 4))

        self._instax_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.win,
            text="Show Instax cameras only",
            variable=self._instax_only,
            command=self._apply_filter,
        ).pack(anchor=tk.W, padx=12, pady=(0, 6))

        # Device list
        cols = ("name", "address", "model", "rssi")
        frame = ttk.Frame(self.win)
        frame.pack(fill=tk.BOTH, expand=True, padx=12)

        self._tree = ttk.Treeview(
            frame, columns=cols, show="headings", selectmode="browse"
        )
        self._tree.heading("name",    text="Device name")
        self._tree.heading("address", text="Address")
        self._tree.heading("model",   text="Model hint")
        self._tree.heading("rssi",    text="Signal")
        self._tree.column("name",    width=210, stretch=True)
        self._tree.column("address", width=150, stretch=False)
        self._tree.column("model",   width=130, stretch=False)
        self._tree.column("rssi",    width=70,  stretch=False, anchor=tk.E)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.pack(fill=tk.BOTH, expand=True)

        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self._tree.bind("<Double-1>",          self._on_double_click)

        self._status_var = tk.StringVar(value="Scanning …")
        ttk.Label(
            self.win, textvariable=self._status_var, foreground=DIM
        ).pack(anchor=tk.W, padx=12, pady=(4, 0))

        btn_bar = ttk.Frame(self.win, padding=(8, 8))
        btn_bar.pack(fill=tk.X)

        self.btn_scan = ttk.Button(
            btn_bar, text="Scan Again", command=self._scan, state="disabled"
        )
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_connect = ttk.Button(
            btn_bar, text="Connect", style="Accent.TButton",
            command=self._connect, state="disabled",
        )
        self.btn_connect.pack(side=tk.LEFT)

        ttk.Button(
            btn_bar, text="Cancel", command=self.win.destroy
        ).pack(side=tk.RIGHT)

    # ── actions ───────────────────────────────────────────────────────────────

    def _scan(self):
        self.btn_scan.configure(state="disabled", text="Scanning …")
        self.btn_connect.configure(state="disabled")
        self._tree.delete(*self._tree.get_children())
        self._status_var.set("Scanning … (8 s)")
        self._backend.send_cmd("scan")

    def _apply_filter(self):
        self._tree.delete(*self._tree.get_children())
        instax_only = self._instax_only.get()
        for d in self._all_devices:
            if instax_only and "INSTAX" not in d["name"].upper():
                continue
            rssi_txt = f"{d['rssi']} dBm" if d["rssi"] is not None else "?"
            self._tree.insert(
                "", tk.END, iid=d["address"],
                values=(
                    d["name"] or "(no name)",
                    d["address"],
                    _guess_model(d["name"]),
                    rssi_txt,
                ),
            )

    def _connect(self):
        sel = self._tree.selection()
        if not sel:
            return
        address = sel[0]   # iid == address string
        self.win.destroy()
        self._on_connect(address)

    def _on_select(self, _event=None):
        self.btn_connect.configure(
            state="normal" if self._tree.selection() else "disabled"
        )

    def _on_double_click(self, _event=None):
        if self._tree.selection():
            self._connect()

    # ── callbacks from InstaxApp._handle ─────────────────────────────────────

    def on_scan_done(self, devices: list[dict]):
        self._all_devices = devices
        self._apply_filter()
        n     = len(self._tree.get_children())
        total = len(devices)
        if total == 0:
            self._status_var.set("No devices found — click Scan Again")
        else:
            shown = "all" if n == total else f"{n} of {total}"
            self._status_var.set(f"{total} device(s) found  ({shown} shown)")
        self.btn_scan.configure(state="normal", text="Scan Again")

    def on_scan_error(self, msg: str):
        self._status_var.set(f"Scan error: {msg}")
        self.btn_scan.configure(state="normal", text="Scan Again")


# ══════════════════════════════════════════════════════════════════════════════
# GUI
# ══════════════════════════════════════════════════════════════════════════════

class InstaxApp:

    def __init__(self, address: str | None = None):
        # If no address is given the scan dialog will supply one before connect.
        init_addr = address or DEFAULT_ADDR

        self.ui_q    = queue.Queue()
        self.backend = CameraBackend(self.ui_q, init_addr)
        self.backend.start()

        self._xfer_count = 0
        self._liveview_win: tk.Toplevel | None = None
        self._liveview_lbl: tk.Label  | None = None
        self._liveview_ref = None
        self._liveview_frame_data: bytes | None = None  # last raw JPEG received
        self._thumb_ref    = None
        self._img_w = 0
        self._img_h = 0
        self._print_win: tk.Toplevel | None = None
        self._scan_dlg: ScanDialog | None = None

        self._build_ui()
        self.root.after(33, self._poll_queue)
        # If no address was passed, open the scanner immediately.
        if address is None:
            self.root.after(200, self._connect)
        self.root.mainloop()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("Instax BLE Monitor")
        self.root.geometry("960x620")
        self.root.configure(bg=BG)
        self.root.minsize(700, 450)

        self._apply_style()

        # ── toolbar ───────────────────────────────────────────────────────────
        bar = ttk.Frame(self.root, style="Bar.TFrame", padding=(8, 6))
        bar.pack(fill=tk.X, side=tk.TOP)

        self.btn_connect = ttk.Button(
            bar, text="Scan / Connect", style="Accent.TButton", command=self._connect
        )
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_disconnect = ttk.Button(
            bar, text="Disconnect", command=self._disconnect, state="disabled"
        )
        self.btn_disconnect.pack(side=tk.LEFT, padx=(0, 12))

        self.btn_liveview = ttk.Button(
            bar, text="Live View", command=self._open_liveview, state="disabled"
        )
        self.btn_liveview.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_print = ttk.Button(
            bar, text="Print Image", command=self._open_print_dialog,
            state="disabled"
        )
        self.btn_print.pack(side=tk.LEFT, padx=(0, 4))

        self._status_var = tk.StringVar(value="⬤  Disconnected")
        self._status_lbl = ttk.Label(
            bar, textvariable=self._status_var, foreground=DIM
        )
        self._status_lbl.pack(side=tk.RIGHT, padx=8)

        # ── paned layout ──────────────────────────────────────────────────────
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(2, 6))

        # LEFT PANEL
        left = ttk.Frame(paned, width=270)
        paned.add(left, weight=0)
        left.pack_propagate(False)

        info_frame = ttk.LabelFrame(left, text="Camera", padding=10)
        info_frame.pack(fill=tk.X, padx=4, pady=4)

        self._info_vars: dict[str, tk.StringVar] = {}
        for key, label in [
            ("model",       "Model"),
            ("serial",      "Serial"),
            ("battery_pct", "Battery"),
            ("photos_left", "Photos left"),
        ]:
            row = ttk.Frame(info_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(
                row, text=f"{label}:", width=12, foreground=DIM, anchor=tk.W
            ).pack(side=tk.LEFT)
            v = tk.StringVar(value="—")
            ttk.Label(row, textvariable=v, anchor=tk.W).pack(
                side=tk.LEFT, fill=tk.X
            )
            self._info_vars[key] = v

        xf = ttk.LabelFrame(left, text="Transfer", padding=10)
        xf.pack(fill=tk.X, padx=4, pady=4)

        self._xfer_status_var = tk.StringVar(value="Waiting ...")
        ttk.Label(
            xf, textvariable=self._xfer_status_var, foreground=DIM
        ).pack(anchor=tk.W)

        self._xfer_bar = ttk.Progressbar(
            xf, length=220, mode="determinate", maximum=100
        )
        self._xfer_bar.pack(fill=tk.X, pady=(4, 0))

        self._xfer_label_var = tk.StringVar(value="0 images transferred")
        ttk.Label(
            xf, textvariable=self._xfer_label_var, foreground=DIM
        ).pack(anchor=tk.W, pady=(4, 0))

        thumb_lf = ttk.LabelFrame(left, text="Last Image", padding=6)
        thumb_lf.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._thumb_lbl = tk.Label(
            thumb_lf, text="No image yet", bg=BG3, fg=DIM
        )
        self._thumb_lbl.pack(expand=True, fill=tk.BOTH)

        # RIGHT PANEL — log
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        log_lf = ttk.LabelFrame(right, text="Console Log", padding=4)
        log_lf.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self._log_text = tk.Text(
            log_lf,
            bg="#1a1a1a", fg=FG,
            font=("Consolas", 9),
            state="disabled",
            wrap=tk.WORD,
            relief="flat",
            borderwidth=0,
            cursor="arrow",
        )
        sb = ttk.Scrollbar(log_lf, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._log_text.pack(fill=tk.BOTH, expand=True)

        self._log_text.tag_configure("info", foreground=FG)
        self._log_text.tag_configure("ok",   foreground=OK)
        self._log_text.tag_configure("warn", foreground=WARN)
        self._log_text.tag_configure("err",  foreground=ERR)
        self._log_text.tag_configure("dim",  foreground=DIM)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, bordercolor=BG3,
                    troughcolor=BG3, selectbackground=ACC)
        s.configure("TFrame",        background=BG)
        s.configure("Bar.TFrame",    background=BG2)
        s.configure("TLabel",        background=BG, foreground=FG)
        s.configure("TLabelframe",   background=BG, bordercolor=BG3,
                    foreground=DIM)
        s.configure("TLabelframe.Label", background=BG, foreground=DIM)
        s.configure("TButton",       background=BG3, foreground=FG,
                    borderwidth=1, relief="flat", padding=(8, 4))
        s.map("TButton",
              background=[("active", "#505050"), ("disabled", "#2a2a2a")],
              foreground=[("disabled", "#555555")])
        s.configure("Accent.TButton", background=ACC, foreground="#ffffff",
                    padding=(8, 4))
        s.map("Accent.TButton",
              background=[("active", "#1177bb"), ("disabled", "#1e3d5a")])
        s.configure("TProgressbar",  troughcolor=BG3, background=ACC,
                    borderwidth=0)
        s.configure("TPanedwindow",  background=BG)
        s.configure("Sash",          sashrelief="flat", sashwidth=4,
                    background=BG3)
        s.configure("TScrollbar",    background=BG3, troughcolor=BG,
                    arrowcolor=DIM, borderwidth=0)

    # ── queue polling ─────────────────────────────────────────────────────────

    def _poll_queue(self):
        while True:
            try:
                msg = self.ui_q.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle(msg)
            except Exception as e:
                print(f"[UI handler error] {e}")
        self.root.after(33, self._poll_queue)

    # ── message handler ───────────────────────────────────────────────────────

    def _handle(self, msg: dict):
        kind = msg["kind"]

        if kind == "log":
            self._append_log(msg["text"])

        elif kind == "status":
            state = msg.get("state")
            if state == "connecting":
                self._status_var.set("⬤  Connecting …")
                self._status_lbl.configure(foreground=WARN)
                self.btn_connect.configure(state="disabled")
            elif state == "connected":
                self._status_var.set("⬤  Connected")
                self._status_lbl.configure(foreground=OK)
                self.btn_disconnect.configure(state="normal")
                self.btn_liveview.configure(state="normal")
                self.btn_print.configure(state="normal")
            elif state in ("disconnected", "error"):
                label = (
                    "⬤  Disconnected" if state == "disconnected"
                    else f"⬤  Error: {msg.get('msg', '')}"
                )
                self._status_var.set(label)
                self._status_lbl.configure(foreground=DIM)
                self.btn_connect.configure(state="normal")
                self.btn_disconnect.configure(state="disabled")
                self.btn_liveview.configure(state="disabled")
                self.btn_print.configure(state="disabled")

        elif kind == "camera_info":
            bat = msg.get("battery_pct", "?")
            pht = msg.get("photos_left", "?")
            if msg.get("model"):   self._info_vars["model"].set(msg["model"])
            if msg.get("serial"):  self._info_vars["serial"].set(msg["serial"])
            if bat != "?":         self._info_vars["battery_pct"].set(f"{bat}%")
            if pht != "?":         self._info_vars["photos_left"].set(str(pht))
            if msg.get("img_w"):   self._img_w = msg["img_w"]
            if msg.get("img_h"):   self._img_h = msg["img_h"]

        elif kind == "transfer_start":
            self._xfer_status_var.set("Pulling …")
            self._xfer_bar["value"] = 0

        elif kind == "transfer_meta":
            chunks = msg.get("chunks", 1)
            self._xfer_bar["maximum"] = chunks
            self._xfer_status_var.set(
                f"{msg.get('timestamp', '')}  ·  {msg.get('total', 0):,} B"
            )

        elif kind == "transfer_progress":
            self._xfer_bar["value"] = msg.get("chunk", 0) + 1

        elif kind == "transfer_done":
            self._xfer_count += 1
            self._xfer_label_var.set(f"{self._xfer_count} image(s) transferred")
            self._xfer_status_var.set(f"✓  {Path(msg['path']).name}")
            self._xfer_bar["value"] = self._xfer_bar["maximum"]
            self._show_thumb(msg["path"])

        elif kind == "liveview_frame":
            self._update_liveview(msg["data"])

        elif kind == "print_start":
            if self._print_win and self._print_win.winfo_exists():
                self._print_win._on_print_start(msg["total_chunks"])

        elif kind == "print_chunk":
            if self._print_win and self._print_win.winfo_exists():
                self._print_win._on_print_chunk(msg["chunk"], msg["total_chunks"])

        elif kind == "print_done":
            if self._print_win and self._print_win.winfo_exists():
                self._print_win._on_print_done(msg["printed"])

        elif kind == "print_error":
            if self._print_win and self._print_win.winfo_exists():
                self._print_win._on_print_error(msg["msg"])
            else:
                messagebox.showerror("Print Error", msg["msg"], parent=self.root)

        elif kind == "scan_done":
            if self._scan_dlg and self._scan_dlg.win.winfo_exists():
                self._scan_dlg.on_scan_done(msg["devices"])

        elif kind == "scan_error":
            if self._scan_dlg and self._scan_dlg.win.winfo_exists():
                self._scan_dlg.on_scan_error(msg["msg"])
            else:
                messagebox.showerror("Scan Error", msg["msg"], parent=self.root)

    # ── log helpers ───────────────────────────────────────────────────────────

    def _append_log(self, text: str):
        tl = text.lower()
        if any(w in tl for w in ("error", "failed", "timeout", "no jpeg")):
            tag = "err"
        elif any(w in tl for w in ("saved", "connected", "paired", "complete",
                                    "subscribed", "ok")):
            tag = "ok"
        elif any(w in tl for w in ("ready", "transfer", "→", "←", "pulling")):
            tag = "warn"
        elif any(w in tl for w in ("chunk", "flag=", "scanning", "polling",
                                    "attempt", "pair")):
            tag = "dim"
        else:
            tag = "info"

        ts = time.strftime("%H:%M:%S")
        self._log_text.configure(state="normal")
        self._log_text.insert(tk.END, f"[{ts}]  {text}\n", tag)
        self._log_text.see(tk.END)
        self._log_text.configure(state="disabled")

    # ── thumbnail ─────────────────────────────────────────────────────────────

    def _show_thumb(self, path: str):
        try:
            img = Image.open(path)
            img.thumbnail((240, 180))
            photo = ImageTk.PhotoImage(img)
            self._thumb_lbl.configure(image=photo, text="")
            self._thumb_ref = photo
        except Exception as e:
            self._thumb_lbl.configure(text=f"Preview error: {e}", image="")

    # ── live view window ──────────────────────────────────────────────────────

    def _open_liveview(self):
        if self._liveview_win and self._liveview_win.winfo_exists():
            self._liveview_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Live View")
        win.geometry("680x520")
        win.configure(bg=BG)
        self._liveview_win = win

        lbl = tk.Label(
            win,
            bg=BG2,
            text=(
                "No frame yet\n\n"
                "Press  Start  to begin live view,\n"
                "or wait for the next image transfer."
            ),
            fg=DIM, font=("Segoe UI", 11), justify=tk.CENTER,
        )
        lbl.pack(expand=True, fill=tk.BOTH, padx=4, pady=4)
        self._liveview_lbl = lbl

        btn_bar = ttk.Frame(win, padding=(8, 4))
        btn_bar.pack(fill=tk.X)
        ttk.Button(
            btn_bar, text="Start Live Feed", style="Accent.TButton",
            command=lambda: self.backend.send_cmd("liveview_start"),
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            btn_bar, text="Stop",
            command=lambda: self.backend.send_cmd("liveview_stop"),
        ).pack(side=tk.LEFT, padx=(0, 12))

        # ── Flash control ─────────────────────────────────────────────────────
        ttk.Separator(btn_bar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 8), pady=4
        )
        ttk.Label(btn_bar, text="Flash:").pack(side=tk.LEFT, padx=(0, 4))
        flash_var = tk.IntVar(value=0)

        def _on_flash_change(*_):
            self.backend.send_cmd("set_flash", value=flash_var.get())

        flash_var.trace_add("write", _on_flash_change)
        for label, val in (("AUTO", 0), ("ON", 1), ("OFF", 2)):
            ttk.Radiobutton(
                btn_bar, text=label, variable=flash_var, value=val,
            ).pack(side=tk.LEFT)

        # ── Capture / save buttons ────────────────────────────────────────────
        ttk.Separator(btn_bar, orient=tk.VERTICAL).pack(
            side=tk.LEFT, fill=tk.Y, padx=(8, 8), pady=4
        )
        ttk.Button(
            btn_bar, text="Download Photo",
            command=lambda: self.backend.send_cmd("download_photo"),
        ).pack(side=tk.LEFT, padx=(0, 4))

        def _save_frame():
            data = self._liveview_frame_data
            if not data:
                messagebox.showinfo(
                    "No frame", "No live view frame received yet.", parent=win
                )
                return
            path = filedialog.asksaveasfilename(
                title="Save live view frame",
                parent=win,
                defaultextension=".jpg",
                filetypes=[("JPEG", "*.jpg *.jpeg"), ("All files", "*.*")],
            )
            if path:
                with open(path, "wb") as fh:
                    fh.write(data)

        ttk.Button(btn_bar, text="Save Frame", command=_save_frame).pack(side=tk.LEFT)

        def _on_close():
            self.backend.send_cmd("liveview_stop")
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _update_liveview(self, data: bytes):
        if not self._liveview_lbl or not self._liveview_lbl.winfo_exists():
            return
        self._liveview_frame_data = data   # keep latest JPEG for Save Frame
        try:
            img   = Image.open(BytesIO(data))
            win_w = self._liveview_win.winfo_width()  or 640
            win_h = self._liveview_win.winfo_height() or 460
            img.thumbnail((max(win_w - 8, 320), max(win_h - 60, 200)))
            photo = ImageTk.PhotoImage(img)
            self._liveview_lbl.configure(image=photo, text="")
            self._liveview_ref = photo
        except Exception:
            pass

    # ── print dialog ──────────────────────────────────────────────────────────

    def _open_print_dialog(self):
        if self._img_w == 0:
            messagebox.showwarning(
                "Dimensions unknown",
                "Camera dimensions not yet retrieved.\n"
                "Reconnect and wait for status.",
                parent=self.root,
            )
            return

        path = filedialog.askopenfilename(
            title="Select image to print",
            parent=self.root,
            filetypes=[
                ("Images", "*.jpg *.jpeg *.png *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        if self._print_win and self._print_win.winfo_exists():
            self._print_win.destroy()

        win = tk.Toplevel(self.root)
        win.title("Print Image")
        win.resizable(False, False)
        win.configure(bg=BG)
        win.grab_set()
        self._print_win = win

        w, h = self._img_w, self._img_h
        preview_w = 240
        preview_h = int(240 * h / w) if w > 0 else 320
        if preview_h > 320:
            preview_h = 320
            preview_w = int(320 * w / h)

        try:
            src = Image.open(path).convert("RGB")
            src.thumbnail((w, h), Image.Resampling.LANCZOS)
            canvas_img = Image.new("RGB", (w, h), (0, 0, 0))
            canvas_img.paste(src, ((w - src.width) // 2, (h - src.height) // 2))
            canvas_img.thumbnail((preview_w, preview_h), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(canvas_img)
        except Exception as ex:
            messagebox.showerror("Preview failed", str(ex), parent=self.root)
            win.destroy()
            return

        win._photo_ref = photo

        frm = ttk.Frame(win, padding=12)
        frm.pack()

        tk.Label(frm, image=photo, bg=BG2).grid(
            row=0, column=0, columnspan=2, pady=(0, 8)
        )

        dim_txt = (
            f"Target: {w}×{h} px"
            f"  ({'portrait' if h > w else 'landscape'})"
        )
        ttk.Label(frm, text=dim_txt, foreground=DIM).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 4)
        )
        ttk.Label(frm, text=Path(path).name, foreground=FG).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(0, 8)
        )

        eject_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm, text="Eject film (physically print)", variable=eject_var
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        win._progress   = ttk.Progressbar(
            frm, length=preview_w + 10, mode="determinate", maximum=100
        )
        win._status_var = tk.StringVar(value="")
        win._status_lbl = ttk.Label(
            frm, textvariable=win._status_var, foreground=DIM
        )

        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=(4, 0))

        win._send_btn = ttk.Button(
            btn_frame, text="Send to Camera", style="Accent.TButton",
            command=lambda: _do_send(),
        )
        win._send_btn.pack(side=tk.LEFT, padx=(0, 6))
        win._cancel_btn = ttk.Button(
            btn_frame, text="Cancel", command=win.destroy
        )
        win._cancel_btn.pack(side=tk.LEFT)

        def _do_send():
            win._send_btn.configure(state="disabled")
            win._cancel_btn.configure(state="disabled")
            win._progress.grid(
                row=4, column=0, columnspan=2, sticky=tk.EW, pady=(0, 2)
            )
            win._status_lbl.grid(
                row=5, column=0, columnspan=2, sticky=tk.W, pady=(0, 4)
            )
            win._status_var.set("Sending ...")
            self.backend.send_cmd(
                "print_image", path=path, enable_print=eject_var.get()
            )

        def _on_print_start(total):
            win._progress["maximum"] = total
            win._progress["value"]   = 0
            win._status_var.set(f"Sending 0/{total} chunks ...")
        win._on_print_start = _on_print_start

        def _on_print_chunk(chunk, total):
            win._progress["value"] = chunk + 1
            win._status_var.set(f"Sending {chunk + 1}/{total} chunks ...")
        win._on_print_chunk = _on_print_chunk

        def _on_print_done(printed):
            win._progress["value"] = win._progress["maximum"]
            msg = "Film ejected — printing!" if printed else "Data sent (no ejection)"
            win._status_var.set(msg)
            win._cancel_btn.configure(state="normal", text="Close")
        win._on_print_done = _on_print_done

        def _on_print_error(err):
            win._status_var.set(f"Error: {err}")
            win._status_lbl.configure(foreground=ERR)
            win._send_btn.configure(state="normal")
            win._cancel_btn.configure(state="normal")
        win._on_print_error = _on_print_error

    # ── toolbar actions ───────────────────────────────────────────────────────

    def _connect(self):
        """Open the BLE scan dialog; the dialog calls _do_connect on selection."""
        if self._scan_dlg and self._scan_dlg.win.winfo_exists():
            self._scan_dlg.win.lift()
            return
        self._scan_dlg = ScanDialog(self.root, self.backend, self._do_connect)

    def _do_connect(self, address: str):
        """Called by ScanDialog when the user confirms a camera."""
        self._scan_dlg = None
        self.backend.send_cmd("set_address", address=address)
        self.backend.send_cmd("connect")
        self.btn_connect.configure(state="disabled")
        self.root.title(f"Instax BLE Monitor  —  {address}")

    def _disconnect(self):
        self.backend.send_cmd("disconnect")

    def _on_close(self):
        self.backend.send_cmd("stop")
        self.root.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    addr = sys.argv[1].upper() if len(sys.argv) > 1 else None
    InstaxApp(address=addr)


if __name__ == "__main__":
    main()
