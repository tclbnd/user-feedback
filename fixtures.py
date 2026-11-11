"""Synthetic capture-log fixtures for the event recorder.

The baseline :func:`generate` keeps its original CLI shape and content.  The
scenario builders add every recovery case the recorder must handle:

* ``reorder``  - fragments arrive out of order and are redelivered;
* ``truncated`` - a first delivery is cut short and later completed;
* ``missing``  - a sealed generation never receives one fragment per track;
* ``reset``    - a capture restart zeroes timestamps in a new generation that
  also changes the codec configuration (new init sections);
* ``conflict`` - the same fragment id is delivered with different content.

All media is synthesized locally with ffmpeg; no network access is required.
"""

import argparse
import hashlib
import json
import shutil
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


def _ffmpeg():
    return [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y"]


def _run_video(destination: Path, duration: str, rate: str = "30000/1001",
               width: int = 160, height: int = 90, gop: int = 60) -> None:
    flags = "+empty_moov+default_base_moof"
    subprocess.run(_ffmpeg() + [
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:rate={rate}", "-t", duration,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(gop),
        "-keyint_min", str(gop), "-sc_threshold", "0", "-bf", "2",
        "-x264-params", "open-gop=0",
        "-video_track_timescale", "90000",
        "-movflags", flags + "+frag_keyframe", str(destination),
    ], check=True)


def _run_audio(destination: Path, duration: str, freq: int = 997) -> None:
    flags = "+empty_moov+default_base_moof"
    subprocess.run(_ffmpeg() + [
        "-f", "lavfi", "-i",
        f"sine=frequency={freq}:sample_rate=48000", "-t", duration,
        "-c:a", "aac", "-b:a", "64k",
        "-movflags", flags, "-frag_duration", "2000000", str(destination),
    ], check=True)


def split_media(path, output, track, epoch="recording-0"):
    output.mkdir(parents=True, exist_ok=True)
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
            "type": "fragment", "epoch": epoch, "track": track,
            "seq": number, "path": media.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "summary": f"{track} fragment {number} ({len(data)} bytes)",
        })
    return init_path.name, entries


def _fragment_event(epoch, track, seq, path: Path, summary=None,
                    base: Path = None):
    absolute = path if base is None else base / path
    data = absolute.read_bytes()
    stored_path = path.as_posix() if base is not None else path.name
    return {
        "type": "fragment", "epoch": epoch, "track": track, "seq": seq,
        "path": stored_path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "summary": summary or f"{track} fragment {seq} ({len(data)} bytes)",
    }


def _open_event(epoch, video_init, audio_init,
                v_origin=0, a_origin=0):
    return {
        "type": "open", "epoch": epoch,
        "tracks": {
            "video": {"init": Path(video_init).as_posix(),
                      "timescale": 90000, "clock_origin": v_origin},
            "audio": {"init": Path(audio_init).as_posix(),
                      "timescale": 48000, "clock_origin": a_origin},
        },
    }


def _seal_event(epoch, video_count, audio_count):
    return {
        "type": "seal", "epoch": epoch,
        "last": {"video": video_count, "audio": audio_count},
    }


def _write_log(output: Path, events) -> Path:
    log = output / "capture.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in events),
                   encoding="utf-8")
    return log


def generate(output: Path) -> Path:
    """Original baseline fixture: one generation, fragments in order."""
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp:
        video = Path(temp) / "video.mp4"
        audio = Path(temp) / "audio.mp4"
        _run_video(video, "6.006")
        _run_audio(audio, "6.006")
        video_init, video_entries = split_media(video, output, "video")
        audio_init, audio_entries = split_media(audio, output, "audio")
    events = [
        _open_event("recording-0", video_init, audio_init),
        *video_entries, *audio_entries,
        _seal_event("recording-0", len(video_entries), len(audio_entries)),
    ]
    return _write_log(output, events)


# ---------------------------------------------------------------------------
# Scenario fixtures
# ---------------------------------------------------------------------------

def _build_base_tracks(output: Path, duration="6.006"):
    """Generate the standard per-track fragment set and return file lists."""
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp:
        video = Path(temp) / "video.mp4"
        audio = Path(temp) / "audio.mp4"
        _run_video(video, duration)
        _run_audio(audio, duration)
        video_init, _ = split_media(video, output, "video")
        audio_init, _ = split_media(audio, output, "audio")
    video_files = sorted(output.glob("video-0*.m4s"))
    audio_files = sorted(output.glob("audio-0*.m4s"))
    return video_init, audio_init, video_files, audio_files


def generate_reorder(output: Path) -> Path:
    """Out-of-order arrival plus duplicate redelivery (idempotent result)."""
    video_init, audio_init, vf, af = _build_base_tracks(output)
    events = [_open_event("recording-0", video_init, audio_init,
                          v_origin=9000, a_origin=4800)]

    def shuffled(files, track):
        # Deterministic permutation that stays inside the 4-fragment window:
        # deliver 1,3,2,5,4,... and then redeliver an earlier fragment.
        order = []
        for index in range(0, len(files), 2):
            order.append(index)
            if index + 1 < len(files):
                order.append(index + 1)
            if index >= 2:
                order.append(index - 1)  # redelivery of an earlier number
        seen = set()
        for idx in order:
            seq = idx + 1
            events.append(_fragment_event(
                "recording-0", track, seq, files[idx],
                summary=f"{track} seq {seq} arrival slot {len(seen)}"))
            seen.add(seq)

    shuffled(vf, "video")
    shuffled(af, "audio")
    events.append(_seal_event("recording-0", len(vf), len(af)))
    return _write_log(output, events)


def generate_truncated(output: Path) -> Path:
    """First delivery of video seq 2 and audio seq 2 is truncated, then fixed."""
    video_init, audio_init, vf, af = _build_base_tracks(output)
    events = [_open_event("recording-0", video_init, audio_init)]

    def truncated_chain(files, track):
        for index, path in enumerate(files):
            seq = index + 1
            if seq == 2:
                full = path.read_bytes()
                cut = output / f"{track}-{seq:03d}-truncated.m4s"
                cut.write_bytes(full[: len(full) // 2])
                bad_event = _fragment_event(
                    "recording-0", track, seq, cut,
                    summary=f"{track} seq {seq} truncated first delivery")
                # Claim the real (full) checksum while the file is cut short.
                bad_event["sha256"] = hashlib.sha256(full).hexdigest()
                events.append(bad_event)
                # Complete redelivery under the same sequence number.
                events.append(_fragment_event(
                    "recording-0", track, seq, path,
                    summary=f"{track} seq {seq} complete redelivery"))
            else:
                events.append(_fragment_event(
                    "recording-0", track, seq, path))

    truncated_chain(vf, "video")
    truncated_chain(af, "audio")
    events.append(_seal_event("recording-0", len(vf), len(af)))
    return _write_log(output, events)


def generate_missing(output: Path) -> Path:
    """Permanent gap: video seq 2 and audio seq 2 never arrive before seal.

    A longer capture keeps plenty of media after the recovery boundary so the
    recovered segments carry re-aligned audio rather than ending in silence.
    """
    video_init, audio_init, vf, af = _build_base_tracks(output, duration="12.012")
    events = [_open_event("recording-0", video_init, audio_init)]
    for index, path in enumerate(vf):
        if index + 1 == 2:
            continue
        events.append(_fragment_event("recording-0", "video", index + 1, path))
    for index, path in enumerate(af):
        if index + 1 == 2:
            continue
        events.append(_fragment_event("recording-0", "audio", index + 1, path))
    events.append(_seal_event("recording-0", len(vf), len(af)))
    return _write_log(output, events)


def generate_reset(output: Path) -> Path:
    """Two generations; the second restarts timestamps at zero with a new
    codec configuration (different resolution / GOP), forcing new init maps."""
    output.mkdir(parents=True, exist_ok=True)
    events = []

    # Generation 0: standard capture.
    with TemporaryDirectory() as temp:
        v0 = Path(temp) / "v0.mp4"
        a0 = Path(temp) / "a0.mp4"
        _run_video(v0, "4.004", width=160, height=90, gop=60)
        _run_audio(a0, "4.004", freq=997)
        v0_init, _ = split_media(v0, output / "gen0", "video", "recording-0")
        a0_init, _ = split_media(a0, output / "gen0", "audio", "recording-0")
    gen0 = output / "gen0"
    v0_files = sorted(gen0.glob("video-0*.m4s"))
    a0_files = sorted(gen0.glob("audio-0*.m4s"))
    events.append(_open_event("recording-0",
                              Path("gen0") / Path(v0_init).name,
                              Path("gen0") / Path(a0_init).name))
    for i, p in enumerate(v0_files, 1):
        events.append(_fragment_event("recording-0", "video", i,
                                      Path("gen0") / p.name, base=output))
    for i, p in enumerate(a0_files, 1):
        events.append(_fragment_event("recording-0", "audio", i,
                                      Path("gen0") / p.name, base=output))
    events.append(_seal_event("recording-0", len(v0_files), len(a0_files)))

    # Generation 1: capture restart, timestamps reset, new configuration.
    with TemporaryDirectory() as temp:
        v1 = Path(temp) / "v1.mp4"
        a1 = Path(temp) / "a1.mp4"
        _run_video(v1, "4.004", width=128, height=96, gop=30)
        _run_audio(a1, "4.004", freq=1480)
        v1_init, _ = split_media(v1, output / "gen1", "video", "recording-1")
        a1_init, _ = split_media(a1, output / "gen1", "audio", "recording-1")
    gen1 = output / "gen1"
    v1_files = sorted(gen1.glob("video-0*.m4s"))
    a1_files = sorted(gen1.glob("audio-0*.m4s"))
    events.append(_open_event("recording-1",
                              Path("gen1") / Path(v1_init).name,
                              Path("gen1") / Path(a1_init).name))
    for i, p in enumerate(v1_files, 1):
        events.append(_fragment_event("recording-1", "video", i,
                                      Path("gen1") / p.name, base=output))
    for i, p in enumerate(a1_files, 1):
        events.append(_fragment_event("recording-1", "audio", i,
                                      Path("gen1") / p.name, base=output))
    events.append(_seal_event("recording-1", len(v1_files), len(a1_files)))
    return _write_log(output, events)


def generate_conflict(output: Path) -> Path:
    """Same fragment id delivered twice with different, valid content."""
    video_init, audio_init, vf, af = _build_base_tracks(output)
    events = [_open_event("recording-0", video_init, audio_init)]
    for index, path in enumerate(vf):
        events.append(_fragment_event("recording-0", "video", index + 1, path))
    for index, path in enumerate(af):
        events.append(_fragment_event("recording-0", "audio", index + 1, path))
    # A second, genuinely different media file claims video seq 1.
    conflict = output / "video-001-conflict.m4s"
    shutil.copyfile(vf[2], conflict)
    events.append(_fragment_event(
        "recording-0", "video", 1, conflict,
        summary="conflicting redelivery of video seq 1"))
    events.append(_seal_event("recording-0", len(vf), len(af)))
    return _write_log(output, events)


SCENARIOS = {
    "sample": generate,
    "reorder": generate_reorder,
    "truncated": generate_truncated,
    "missing": generate_missing,
    "reset": generate_reset,
    "conflict": generate_conflict,
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    log = SCENARIOS[args.scenario](args.output)
    print(log)
