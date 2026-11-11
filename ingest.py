import hashlib
import json
from pathlib import Path


def read_capture(log: Path) -> dict:
    session = None
    sealed = False
    for line in log.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        kind = event["type"]
        if kind == "open":
            if session is not None:
                raise ValueError("only one epoch is supported")
            session = {
                "epoch": event["epoch"],
                "tracks": {
                    track: dict(data, init=log.parent / data["init"], fragments=[])
                    for track, data in event["tracks"].items()
                },
            }
        elif kind == "fragment" and session is not None and not sealed:
            if event["epoch"] != session["epoch"]:
                raise ValueError("unexpected epoch")
            track = session["tracks"][event["track"]]
            if event["seq"] != len(track["fragments"]) + 1:
                raise ValueError("fragments must arrive in order")
            media = log.parent / event["path"]
            if hashlib.sha256(media.read_bytes()).hexdigest() != event["sha256"]:
                raise ValueError("fragment checksum mismatch")
            track["fragments"].append(media)
        elif kind == "seal" and session is not None and not sealed:
            if event["epoch"] != session["epoch"]:
                raise ValueError("unexpected seal epoch")
            for name, track in session["tracks"].items():
                if len(track["fragments"]) != event["last"][name]:
                    raise ValueError("incomplete capture")
            sealed = True
        else:
            raise ValueError(f"unexpected event: {kind}")
    if not sealed:
        raise ValueError("a complete capture is required")
    return session
