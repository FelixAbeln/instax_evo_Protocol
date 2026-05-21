"""Base camera path — default behaviour for Wide-Evo-like cameras.

A *camera path* encapsulates the model-specific protocol behaviour: which
operations are supported and how they are implemented on the wire.

``CameraBackend`` selects the right path after reading the model ID at
connect time (via ``cameras.get_path(model_id)``) and then delegates
all camera-specific operations here.  Subclass this for each camera
model and override only the parts that differ.

Overrideable contract
---------------------
  supports_image_pull   — True if (88,xx) phone-initiated pull works
  supports_image_print  — True if (10,xx) phone-to-camera print works
  supports_liveview     — True if (82,xx) viewfinder stream works

  pull_one(backend)     — pull one image; returns True on success
"""

from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path
from typing import Any

from ..protocol import make_packet

_OUT_DIR = Path("captures/image_transfer")


class BaseCameraPath:
    """Default protocol path — assumes Wide-Evo (FI028) capabilities.

    Unknown/unregistered models fall back to this class so the app stays
    functional even when a new model is encountered.
    """

    # Subclasses declare which model IDs map to them.
    model_ids:    tuple[str, ...] = ()
    display_name: str             = "Instax camera (default)"

    # ── feature flags ──────────────────────────────────────────────────────────
    # Set to False in subclasses where the feature is known to be unsupported.
    supports_image_pull:  bool = True   # (88,xx) phone-initiated image pull
    supports_image_print: bool = True   # (10,xx) phone-to-camera image send + print
    supports_liveview:    bool = True   # (82,xx) viewfinder frame stream

    # ── image pull (88,xx) ────────────────────────────────────────────────────

    async def pull_one(self, backend: Any) -> bool:
        """Pull one image from the camera using the (88,xx) protocol.

        Protocol (confirmed on FI028, 2026-05-17):
          (88,00) start   →  5-byte ACK
          (88,01) meta    →  34-byte metadata (total_size, chunk_sz, timestamp…)
          (88,02) × N    →  one Link-protocol frame per chunk [img_idx:4][seq:1][JPEG…]
          (88,03) end     →  1-byte status
          (88,05) done    →  1-byte status

        Returns True on success (image saved to disk), False on any failure.
        Sets ``backend._transfer_supported = False`` on TimeoutError so the
        poll loop stops retrying (88,xx) for the rest of this BLE session.
        """
        try:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            await backend._flush_rx()

            # (88,00) start ───────────────────────────────────────────────────
            # Phone initiates; camera enters transfer mode in response.
            # Expected ack: [00 00 00 00 00].
            # 0x81 = camera not in transfer mode (flag not yet raised) —
            # the caller must ensure CAMERA_FUNCTION_INFO payload[4] != 0 first.
            backend._log("  → (88,00)  start transfer")
            await backend._write(make_packet(0x88, 0x00))
            _, _, ack = await backend._recv_frame(timeout=5.0)
            backend._log(f"  ← (88,00)  ack={ack.hex()}")

            if len(ack) != 5 or ack[0] != 0x00:
                backend._log(
                    f"  (88,00) NACK 0x{ack[0]:02x} — camera not in transfer mode"
                    if ack else "  (88,00) empty ack — aborting"
                )
                return False

            # (88,01) metadata ────────────────────────────────────────────────
            # The camera occasionally returns a short/error response immediately
            # after (88,00) while it's still preparing — retry once after 1 s.
            backend._log("  → (88,01)  request metadata")
            await backend._write(make_packet(0x88, 0x01, b"\x00\x00\x00\x00"))
            _, _, meta = await backend._recv_frame(timeout=5.0)

            if len(meta) < 10:
                backend._log(
                    f"  (88,01) short response ({meta.hex()}) — retrying in 1 s"
                )
                await asyncio.sleep(1.0)
                await backend._flush_rx()
                await backend._write(make_packet(0x88, 0x01, b"\x00\x00\x00\x00"))
                _, _, meta = await backend._recv_frame(timeout=5.0)
                if len(meta) < 10:
                    backend._log(
                        f"  Short/error response to (88,01): {meta.hex()} — aborting"
                    )
                    return False

            total_size    = struct.unpack_from(">I", meta, 1)[0]
            chunk_data_sz = struct.unpack_from(">I", meta, 5)[0]
            ts_raw        = meta[9:23].decode("ascii", errors="replace")
            num_chunks    = (total_size + chunk_data_sz - 1) // chunk_data_sz

            try:
                ts_fmt = (
                    f"{ts_raw[:4]}-{ts_raw[4:6]}-{ts_raw[6:8]} "
                    f"{ts_raw[8:10]}:{ts_raw[10:12]}:{ts_raw[12:14]}"
                )
            except Exception:
                ts_fmt = ts_raw

            backend._log(
                f"  ← (88,01)  {total_size:,} B  {num_chunks} chunks  {ts_fmt}"
            )
            backend._ui(
                "transfer_meta",
                total=total_size, chunks=num_chunks, timestamp=ts_fmt,
            )

            # (88,02) chunks ──────────────────────────────────────────────────
            jpeg = bytearray()
            t0   = time.time()
            for i in range(num_chunks):
                await backend._write(make_packet(0x88, 0x02, struct.pack(">I", i)))
                _, _, chunk = await backend._recv_frame(timeout=10.0)
                jpeg.extend(chunk[5:])   # skip [img_idx:4][seq:1]
                elapsed = time.time() - t0
                backend._log(
                    f"  chunk {i:3d}/{num_chunks - 1}"
                    f"  {len(chunk) - 5}B  ({elapsed:.1f}s)"
                )
                backend._ui("transfer_progress", chunk=i, total_chunks=num_chunks)

            # (88,03) end ─────────────────────────────────────────────────────
            backend._log("  → (88,03)  end of data")
            await backend._write(make_packet(0x88, 0x03))
            _, _, st = await backend._recv_frame(timeout=5.0)
            backend._log(
                f"  ← (88,03)  status=0x{st[0]:02x}" if st
                else "  ← (88,03)  (empty)"
            )

            # (88,05) complete ────────────────────────────────────────────────
            backend._log("  → (88,05)  transfer complete")
            await backend._write(make_packet(0x88, 0x05, b"\x00\x00\x00\x00"))
            _, _, st = await backend._recv_frame(timeout=5.0)
            backend._log(
                f"  ← (88,05)  status=0x{st[0]:02x}" if st
                else "  ← (88,05)  (empty)"
            )

            # Save JPEG ───────────────────────────────────────────────────────
            soi = bytes(jpeg).find(b"\xff\xd8")
            if soi < 0:
                backend._log("  No JPEG SOI found — discarding")
                return True

            jpeg_bytes = bytes(jpeg[soi:])
            fname = (
                f"transfer_{ts_raw[:4]}-{ts_raw[4:6]}-{ts_raw[6:8]}"
                f"_{ts_raw[8:14]}_{int(time.time())}.jpg"
            )
            out = _OUT_DIR / fname
            out.write_bytes(jpeg_bytes)
            backend._log(f"\nSaved {len(jpeg_bytes):,} bytes → {out}")
            backend._ui(
                "transfer_done",
                path=str(out), size=len(jpeg_bytes), timestamp=ts_fmt,
            )
            return True

        except asyncio.TimeoutError:
            backend._log(
                "  Timeout during (88,xx) pull — "
                "disabling image pull for this session"
            )
            backend._transfer_supported = False
            return False

        except Exception as e:
            backend._log(f"  Pull error: {e}")
            return False
