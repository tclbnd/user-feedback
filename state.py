import json
from pathlib import Path


def record_progress(output: Path, session: dict) -> None:
    state = {
        "epoch": session["epoch"],
        "last": {name: len(track["fragments"]) for name, track in session["tracks"].items()},
    }
    (output / "progress.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
