import argparse
import hashlib
import json
import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from mux import ffmpeg_executable


def boxes(data):
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


def split_media(path, output, track):
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
    init_path = output / f"{track}-init.mp4"
    init_path.write_bytes(b"".join(init))
    entries = []
    for number, data in enumerate(fragments, 1):
        media = output / f"{track}-{number:03d}.m4s"
        media.write_bytes(data)
        entries.append({
            "type": "fragment", "epoch": "recording-0", "track": track, "seq": number,
            "path": media.name, "sha256": hashlib.sha256(data).hexdigest(),
        })
    return init_path.name, entries


def generate(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    ffmpeg = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y"]
    flags = "+empty_moov+default_base_moof"
    with TemporaryDirectory() as temp:
        video = Path(temp) / "video.mp4"
        audio = Path(temp) / "audio.mp4"
        subprocess.run(ffmpeg + [
            "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30000/1001", "-t", "6.006",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "60", "-keyint_min", "60",
            "-sc_threshold", "0", "-bf", "2", "-x264-params", "open-gop=0",
            "-video_track_timescale", "90000", "-movflags", flags + "+frag_keyframe", str(video),
        ], check=True)
        subprocess.run(ffmpeg + [
            "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000", "-t", "6.006",
            "-c:a", "aac", "-b:a", "64k", "-movflags", flags, "-frag_duration", "2000000", str(audio),
        ], check=True)
        video_init, video_entries = split_media(video, output, "video")
        audio_init, audio_entries = split_media(audio, output, "audio")
    events = [{
        "type": "open", "epoch": "recording-0",
        "tracks": {
            "video": {"init": video_init, "timescale": 90000, "clock_origin": 0},
            "audio": {"init": audio_init, "timescale": 48000, "clock_origin": 0},
        },
    }, *video_entries, *audio_entries, {
        "type": "seal", "epoch": "recording-0",
        "last": {"video": len(video_entries), "audio": len(audio_entries)},
    }]
    log = output / "capture.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return log


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    generate(parser.parse_args().output)
