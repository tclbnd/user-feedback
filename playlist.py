"""Playlist publication helpers.

All writes land in a temporary name first and are atomically renamed, so a
reader can never observe a half-written playlist or segment.  The actual
crash-safe ordering (segments -> playlist -> progress) lives in
:mod:`mux` and :mod:`state`.
"""

import hashlib
import os
from pathlib import Path

from mux import PLAYLIST_NAME


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def publish(stage: Path, output: Path) -> None:
    """Legacy entry point: copy a fully rendered stage into ``output``."""
    output.mkdir(parents=True, exist_ok=True)
    for source in sorted(stage.glob("*.mp4")) + sorted(stage.glob("*.m4s")):
        target = output / source.name
        if not target.exists() or \
                hashlib.sha256(target.read_bytes()).digest() != \
                hashlib.sha256(source.read_bytes()).digest():
            atomic_write(target, source.read_bytes())
    atomic_write(output / PLAYLIST_NAME,
                 (stage / PLAYLIST_NAME).read_bytes())
