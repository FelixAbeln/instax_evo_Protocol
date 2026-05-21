"""Camera path for the Instax Mini Evo (model FI019, Gen 1).

Key differences from the Wide Evo (FI028):

  * Image pull (88,xx) is NOT supported.  Sending (88,00) causes the
    Mini Evo to drop the BLE connection immediately — no ACK is ever sent.
    The transfer-ready flag (CAMERA_FUNCTION_INFO payload[4]) still appears
    when the user presses the Share button, but we must NOT act on it with
    (88,xx).

  * Live view (82,xx) is supported, but Gen 1 may need a short warm-up
    window after session open where early pulls return short non-JPEG payloads
    (for example payload [0x02]) before full JPEG frames begin.

  * Print (10,xx) works on the same Link framing as FI028 and is treated as
    supported in the maintained app/docs.
"""

from __future__ import annotations

from typing import Any

from .base import BaseCameraPath


class FI019Path(BaseCameraPath):
    """Instax Mini Evo (FI019) — Gen 1."""

    model_ids    = ("FI019",)
    display_name = "Instax Mini Evo (FI019)"

    # ── feature flags ─────────────────────────────────────────────────────────
    supports_image_pull:  bool = False  # (88,xx) causes BLE disconnect — SKIP
    supports_image_print: bool = True   # (10,xx)
    supports_liveview:    bool = True   # (82,xx), with warm-up tolerance

    async def pull_one(self, backend: Any) -> bool:
        """Image pull is not supported on FI019.

        This method is a safety guard — it should never be called because
        the poll loop checks ``supports_image_pull`` before delegating.
        Calling it means the flag check was bypassed; log and bail out.
        """
        backend._log(
            "Image pull not supported on FI019 (Mini Evo) — "
            "(88,xx) causes the camera to drop the BLE connection."
        )
        backend._log(
            "  Tip: transfer-ready flag persists until power-cycle. "
        )
        backend._transfer_supported = False
        return False
