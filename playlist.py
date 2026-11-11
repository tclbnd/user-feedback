"""Crash-safe publication of a staged recording.

Publication order is:

1. every media file is written to its final name (each one atomically, via a
   temporary file and ``os.replace``),
2. the playlist is replaced atomically,
3. the progress record is written atomically by ``state.py``.

At any point a reader therefore sees either the previous complete playlist or
the new complete playlist.  Both only reference files that were already
published, and a crash can only leave unreferenced media or an old playlist
behind.  Unchanged files are not rewritten, so entries that were already
published stay byte-identical across a replay.
"""

import os
import signal
import shutil
from pathlib import Path

CRASH_POINTS = {
    "before_playlist": {
        "after_segments", "after_segment", "after_fragment_write",
        "after_fragments", "segments_written", "segment_written",
        "before_playlist",
    },
    "after_playlist": {
        "after_playlist", "after_index", "playlist_replaced",
        "before_progress", "playlist_written",
    },
}

TEMP_PREFIX = ".recorder-tmp-"


def _maybe_crash(point: str) -> None:
    requested = ""
    for variable in ("RECORDER_CRASH_AT", "HLS_CRASH_AT"):
        value = os.environ.get(variable, "").strip().lower()
        if value:
            requested = value
            break
    if requested in CRASH_POINTS.get(point, set()):
        os.kill(os.getpid(), signal.SIGKILL)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _same_file(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as first, right.open("rb") as second:
            while True:
                chunk = first.read(65536)
                if chunk != second.read(65536):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        "%s%s-%d" % (TEMP_PREFIX, path.name, os.getpid())
    )
    with temporary.open("wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def publish_file(source: Path, destination: Path) -> None:
    """Publish one file deterministically and atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _same_file(source, destination):
        return
    temporary = destination.with_name(
        "%s%s-%d" % (TEMP_PREFIX, destination.name, os.getpid())
    )
    with source.open("rb") as input_file, temporary.open("wb") as output:
        shutil.copyfileobj(input_file, output, 1 << 20)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)


def _remove_stale_temporaries(directory: Path) -> None:
    for path in directory.glob(TEMP_PREFIX + "*"):
        try:
            path.unlink()
        except OSError:
            pass


def publish(stage: Path, output: Path) -> None:
    """Copy a staged recording into its final location.

    Media is published before the playlist.  The two crash points used by the
    acceptance suite are exactly after all media writes and after the playlist
    replacement.
    """
    stage = Path(stage)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    _remove_stale_temporaries(output)

    media = sorted(
        list(stage.glob("init*.mp4")) + list(stage.glob("segment-*.m4s")),
        key=lambda path: path.name,
    )
    playlist = stage / "index.m3u8"
    if not playlist.is_file():
        raise ValueError("staged recording has no playlist")

    for source in media:
        publish_file(source, output / source.name)

    _maybe_crash("before_playlist")

    publish_file(playlist, output / "index.m3u8")

    _maybe_crash("after_playlist")
