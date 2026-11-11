"""Final progress record.

The progress file is written only after the playlist has been replaced.  It is
itself replaced atomically, so a crash can leave an older progress file (or
none) but never a torn one.
"""

import json

from playlist import atomic_write_bytes


def _published_last(epoch: dict) -> dict:
    return {
        name: len(track.get("fragments", []))
        for name, track in epoch["tracks"].items()
    }


def _epoch_state(epoch: dict) -> dict:
    return {
        "epoch": epoch["epoch"],
        "last": _published_last(epoch),
        "expected": dict(epoch.get("last") or {}),
        "missing": {
            name: sorted(track.get("missing", []))
            for name, track in epoch["tracks"].items()
        },
    }


def record_progress(output, session: dict) -> None:
    epochs = session.get("epochs") or []
    if len(epochs) == 1:
        state = {
            "epoch": epochs[0]["epoch"],
            "last": _published_last(epochs[0]),
        }
    else:
        state = {
            "epoch": epochs[-1]["epoch"] if epochs else session.get("epoch"),
            "last": _published_last(epochs[-1]) if epochs else {},
            "epochs": [_epoch_state(epoch) for epoch in epochs],
        }
    atomic_write_bytes(
        output / "progress.json",
        (json.dumps(state) + "\n").encode("utf-8"),
    )
