"""Camera path registry.

Maps model IDs (returned by DEVICE_INFO_SERVICE InfoType=1) to the
appropriate camera path class.  Unknown model IDs fall back to
``BaseCameraPath`` so the application remains usable even when a new
camera model is encountered for the first time.

Usage
-----
    from instax_lab.cameras import get_path

    path = get_path("FI028")     # → FI028Path instance
    path = get_path("FI019")     # → FI019Path instance
    path = get_path("unknown")   # → BaseCameraPath instance (default)

    if path.supports_image_pull:
        ok = await path.pull_one(backend)
"""

from .base  import BaseCameraPath
from .fi028 import FI028Path
from .fi019 import FI019Path

__all__ = ["BaseCameraPath", "FI028Path", "FI019Path", "get_path"]

# Registry: model_id string → camera path class
_REGISTRY: dict[str, type[BaseCameraPath]] = {
    mid: cls
    for cls in (FI028Path, FI019Path)
    for mid in cls.model_ids
}


def get_path(model_id: str) -> BaseCameraPath:
    """Return the camera path instance for *model_id*.

    Falls back to ``BaseCameraPath`` for unrecognised models, so the
    application degrades gracefully (tries (88,xx), may time out and
    disable retries, but won't crash).
    """
    cls = _REGISTRY.get(model_id, BaseCameraPath)
    return cls()
