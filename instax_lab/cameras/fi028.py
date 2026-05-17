"""Camera path for the Instax Evo Wide (model FI028, Gen 2).

Phone-initiated image pull via (88,xx) is fully supported and confirmed
working in live testing (2026-05-17).  All behaviour is inherited from
BaseCameraPath — this file exists to make the capability explicit and to
serve as an extension point for any FI028-specific tweaks.
"""

from .base import BaseCameraPath


class FI028Path(BaseCameraPath):
    """Instax Evo Wide (FI028) — Gen 2."""

    model_ids    = ("FI028",)
    display_name = "Instax Evo Wide (FI028)"

    # ── feature flags (all supported — same as base) ──────────────────────────
    supports_image_pull:  bool = True   # (88,xx) confirmed 2026-05-17
    supports_image_print: bool = True   # (10,xx) confirmed
    supports_liveview:    bool = True   # (82,xx) confirmed 2026-05-17

    # All protocol methods inherited from BaseCameraPath — no overrides needed.
