"""Fixture builders for the geometry mapper tests.

Each fixture is expressed the way the task bundle supplies frame metadata:
coded size, clean aperture (in coded pixels), sample aspect ratio and a
rotation/mirror transform.  The helpers stay deliberately dumb so that the
tests exercise ``geo.py`` rather than a second implementation of it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def frame(
    frame_id: str,
    coded: Sequence[int],
    aperture: Optional[Sequence[int]] = None,
    sar: Sequence[int] = (1, 1),
    transform: Any = None,
    **extra: Any,
) -> Dict[str, Any]:
    value: Dict[str, Any] = {
        "id": frame_id,
        "coded": list(coded),
        "sar": list(sar),
        "transform": transform,
    }
    if aperture is not None:
        value["aperture"] = list(aperture)
    value.update(extra)
    return value


def payload(frames: List[Dict[str, Any]], canvas: Sequence[int],
            **extra: Any) -> Dict[str, Any]:
    value: Dict[str, Any] = {"frames": frames, "canvas": list(canvas)}
    value.update(extra)
    return value


# --- individual acceptance scenarios ---------------------------------------

def portrait_into_landscape(transform: str = "r90") -> Dict[str, Any]:
    """1080x1920 phone capture rotated into a 1920x1080 programme canvas."""
    return payload(
        [frame("p", coded=(1080, 1920), aperture=(0, 0, 1080, 1920),
               sar=(1, 1), transform=transform)],
        canvas=(1920, 1080),
    )


def anamorphic(transform: Any = None) -> Dict[str, Any]:
    """720x576 with 64:45 samples -> 1024x576 square-pixel image."""
    return payload(
        [frame("a", coded=(720, 576), aperture=(0, 0, 720, 576),
               sar=(64, 45), transform=transform)],
        canvas=(1920, 1080),
    )


def odd_boundaries() -> Dict[str, Any]:
    """Odd coded size, odd aperture origin and odd canvas."""
    return payload(
        [frame("odd", coded=(1921, 1081), aperture=(1, 3, 1919, 1075),
               sar=(1, 1), transform="r270")],
        canvas=(1281, 721),
    )


def mirror_with_safe() -> Dict[str, Any]:
    """Left-weighted subtitle-safe rect must move to the right when mirrored."""
    return payload(
        [frame("m", coded=(1920, 1080), aperture=(0, 0, 1920, 1080),
               sar=(1, 1), transform="mirror",
               safe=(0.05, 0.1, 0.4, 0.2))],
        canvas=(1920, 1080),
    )


def out_of_frame() -> Dict[str, Any]:
    """Content deliberately larger than the canvas (fill strategy)."""
    return payload(
        [frame("o", coded=(1920, 1080), aperture=(0, 0, 1920, 1080),
               sar=(1, 1), transform=None)],
        canvas=(100, 100),
        strategy="fill",
    )


def two_frames_different_properties() -> Dict[str, Any]:
    """One clip changes orientation and aperture between frames."""
    return payload(
        [
            frame("f1", coded=(1080, 1920), aperture=(0, 0, 1080, 1920),
                  sar=(1, 1), transform="r90"),
            frame("f2", coded=(1080, 1920), aperture=(0, 0, 1080, 1920),
                  sar=(1, 1), transform="r270"),
            frame("f3", coded=(1440, 1080), aperture=(120, 0, 1200, 1080),
                  sar=(4, 3), transform="mirror"),
        ],
        canvas=(1920, 1080),
    )
