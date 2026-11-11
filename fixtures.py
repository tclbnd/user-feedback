"""Synthetic capture fixtures.

Real media is encoded with FFmpeg (libx264 + AAC).  The script creates
single-track fragmented MP4 files, splits them into one-GOP fragments and
writes a JSONL capture log.  ``build_scenarios`` additionally produces the
capture variants used by ``verify.py``: reordered/duplicated delivery,
truncated-then-complete delivery, permanent holes, a timestamp reset and an
initialisation change.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Optional, Tuple


def ffmpeg_executable() -> str:
    import os
    return os.environ.get("FFMPEG", "ffmpeg")


def boxes(data):
    import struct
    offset = 0
    while offset < len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
        if size == 0:
            size = len(data) - offset
        if size < 8 or offset + size > len(data):
            raise ValueError("invalid MP4 box size")
        yield kind, data[offset:offset + size]
        offset += size


def split_media(path, output, track, prefix=""):
    init = []
    fragments = []
    moof = None
    for kind, data in boxes(path.read_bytes()):
        if kind in (b"ftyp", b"moov"):
            init.append(data)
        elif kind == b"moof":
            moof = data
        elif kind == b"mdat" and moof:
            fragments.append(moof + data)
            moof = None
    init_path = output / f"{prefix}{track}-init.mp4"
    init_path.write_bytes(b"".join(init))
    entries = []
    for number, data in enumerate(fragments, 1):
        media = output / f"{prefix}{track}-{number:03d}.m4s"
        media.write_bytes(data)
        entries.append({
            "type": "fragment", "epoch": "recording-0", "track": track,
            "seq": number, "path": media.name,
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return init_path.name, entries


def _encode_generation(output: Path, prefix: str, size: str, duration: str,
                       epoch: str, clock_origin: Tuple[int, int]) -> List[dict]:
    output.mkdir(parents=True, exist_ok=True)
    ffmpeg = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y"]
    flags = "+empty_moov+default_base_moof"
    with TemporaryDirectory() as temp:
        video = Path(temp) / "video.mp4"
        audio = Path(temp) / "audio.mp4"
        subprocess.run(ffmpeg + [
            "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate=30000/1001", "-t", duration,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "60",
            "-keyint_min", "60", "-sc_threshold", "0", "-bf", "2",
            "-x264-params", "open-gop=0", "-video_track_timescale", "90000",
            "-movflags", flags + "+frag_keyframe", str(video),
        ], check=True)
        subprocess.run(ffmpeg + [
            "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000",
            "-t", duration, "-c:a", "aac", "-b:a", "64k",
            "-movflags", flags, "-frag_duration", "2000000", str(audio),
        ], check=True)
        video_init, video_entries = split_media(video, output, "video", prefix)
        audio_init, audio_entries = split_media(audio, output, "audio", prefix)
    events: List[dict] = [{
        "type": "open", "epoch": epoch,
        "tracks": {
            "video": {"init": video_init, "timescale": 90000,
                      "clock_origin": clock_origin[0]},
            "audio": {"init": audio_init, "timescale": 48000,
                      "clock_origin": clock_origin[1]},
        },
    }]
    for track, entries in (("video", video_entries), ("audio", audio_entries)):
        for entry in entries:
            events.append({
                "type": "fragment", "epoch": epoch, "track": track,
                "seq": entry["seq"], "path": entry["path"],
                "sha256": entry["sha256"],
            })
    events.append({
        "type": "seal", "epoch": epoch,
        "last": {"video": len(video_entries), "audio": len(audio_entries)},
    })
    return events


def generate(output: Path, epoch: str = "recording-0",
             clock_origin: Tuple[int, int] = (0, 0),
             prefix: str = "", size: str = "160x90",
             duration: str = "6.006",
             log_name: Optional[str] = "capture.jsonl") -> Path:
    output = Path(output)
    events = _encode_generation(output, prefix, size, duration, epoch,
                                clock_origin)
    if log_name:
        log = output / log_name
        log.write_text("".join(json.dumps(event) + "\n" for event in events),
                       encoding="utf-8")
        return log
    return output


def _events(path: Path) -> List[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line]


def _write(path: Path, events: List[dict]) -> Path:
    path.write_text("".join(json.dumps(event) + "\n" for event in events),
                    encoding="utf-8")
    return path


def _retarget(events: List[dict], epoch: str) -> List[dict]:
    result = []
    for event in events:
        clone = dict(event)
        if clone.get("epoch") is not None:
            clone["epoch"] = epoch
        result.append(clone)
    return result


def _reordered(events: List[dict]) -> List[dict]:
    opened = events[0]
    sealed = events[-1]
    fragments = events[1:-1]
    # Deliver the second half first, then fill in the earlier slots, and
    # redeliver two already accepted fragments.
    reversed_part = list(reversed(fragments[:8]))
    duplicated = [fragments[0], fragments[3]]
    return [opened] + reversed_part + fragments[8:] + duplicated + [sealed]


def _truncated(events: List[dict], directory: Path) -> List[dict]:
    result = []
    for event in events:
        if (event.get("type") == "fragment" and event.get("track") == "video"
                and event.get("seq") == 2):
            source = directory / event["path"]
            short = directory / "video-002-truncated.m4s"
            short.write_bytes(source.read_bytes()[:-97])
            result.append(dict(event, path=short.name))
        result.append(event)
    return result


def _missing(events: List[dict]) -> List[dict]:
    return [event for event in events
            if not (event.get("type") == "fragment"
                    and event.get("track") == "video"
                    and event.get("seq") == 2)]


def build_scenarios(root: Path) -> Dict[str, Path]:
    """Create all acceptance captures and return ``name -> log path``."""
    root = Path(root)
    source = root / "source"
    source.mkdir(parents=True, exist_ok=True)
    events = _encode_generation(source, "", "160x90", "6.006",
                                "recording-0", (0, 0))
    normal = _write(source / "capture.jsonl", events)
    alt = _encode_generation(source, "alt-", "320x180", "6.006",
                             "recording-1", (0, 0))

    scenarios = {
        "normal": normal,
        "reorder": _write(source / "reorder.jsonl", _reordered(events)),
        "truncated": _write(source / "truncated.jsonl",
                            _truncated(events, source)),
        "missing": _write(source / "missing.jsonl", _missing(events)),
        "reset": _write(source / "reset.jsonl",
                        events + _retarget(events, "recording-1")),
        "init_change": _write(source / "init_change.jsonl", events + alt),
    }
    return scenarios


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--scenarios", action="store_true",
                        help="build every acceptance capture variant")
    arguments = parser.parse_args()
    if arguments.scenarios:
        for name, path in build_scenarios(arguments.output).items():
            print(name, path)
    else:
        print(generate(arguments.output))
