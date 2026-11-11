"""Per-recording-generation, per-packet verification of a publication.

For every retained packet this checks, against the source capture:

* the compressed payload digest (the retained bytes are unchanged);
* the timestamp relationship (DTS and the PTS/DTS composition offset);
* audio/video alignment derived from the two clock origins.

It is used by the acceptance suite and can also be run directly against a
capture directory and a publication directory.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from fmp4 import read_fragment, read_track_runs, trex_defaults
from ingest import CaptureSession, missing_numbers, ordered_fragments


@dataclass
class Packet:
    track: str
    epoch: str
    source_seq: int
    source_index: int
    dts: int
    cto: int
    duration: int
    digest: str


def source_packets(session: CaptureSession) -> Dict[str, List[Packet]]:
    """All retained source packets per track, in decode order, by epoch."""
    result: Dict[str, List[Packet]] = {"video": [], "audio": []}
    for epoch in session.epochs:
        for track in ("video", "audio"):
            spec = epoch.tracks[track]
            defaults = trex_defaults(spec.init_bytes)
            trex = next(iter(defaults.values())) if defaults else None
            for stored in ordered_fragments(epoch, track):
                data = stored.data if stored.data is not None \
                    else stored.path.read_bytes()
                fragment = read_fragment(data, track == "video", trex)
                cursor = fragment.base_media_decode_time
                for index, sample in enumerate(fragment.samples):
                    result[track].append(Packet(
                        track=track, epoch=epoch.epoch,
                        source_seq=stored.seq, source_index=index,
                        dts=cursor, cto=sample.cto,
                        duration=sample.duration,
                        digest=hashlib.sha256(sample.payload).hexdigest()))
                    cursor += sample.duration
    return result


def published_packets(output: Path) -> Dict[str, List[Packet]]:
    """Packets parsed back from the published segments."""
    result: Dict[str, List[Packet]] = {"video": [], "audio": []}
    for path in sorted(output.glob("segment-*.m4s")):
        runs = read_track_runs(path.read_bytes())
        for track_id, run in runs.items():
            track = "video" if track_id == 1 else "audio"
            cursor = run.base_media_decode_time
            for index, sample in enumerate(run.samples):
                result[track].append(Packet(
                    track=track, epoch="", source_seq=0,
                    source_index=index, dts=cursor, cto=sample.cto,
                    duration=sample.duration,
                    digest=hashlib.sha256(sample.payload).hexdigest()))
                cursor += sample.duration
    return result


def verify_packets(session: CaptureSession, output: Path) -> None:
    """Raise AssertionError if any retained packet changed or timestamps drift.
    """
    source = source_packets(session)
    published = published_packets(output)
    for track in ("video", "audio"):
        src, pub = source[track], published[track]
        # Same number of retained packets, same payloads, same per-packet
        # composition offset and duration (decode order and framing preserved).
        assert len(src) == len(pub), \
            f"{track}: retained {len(src)} vs published {len(pub)} packets"
        for index, (a, b) in enumerate(zip(src, pub)):
            assert a.digest == b.digest, \
                f"{track} packet {index}: payload digest changed"
            assert a.cto == b.cto, f"{track} packet {index}: CTO changed"
            assert a.duration == b.duration, \
                f"{track} packet {index}: duration changed"

    # Timestamp relationship: DTS strictly monotonic per track.
    for track in ("video", "audio"):
        dts_values = [p.dts for p in published[track]]
        assert dts_values == sorted(dts_values), \
            f"{track}: published DTS not monotonic"

    # Audio/video alignment on the shared wall-clock timeline.
    first_epoch = session.epochs[0]
    vt = first_epoch.tracks["video"].timescale
    at = first_epoch.tracks["audio"].timescale
    v0 = published["video"][0].dts / vt
    a0 = published["audio"][0].dts / at
    assert abs(v0 - a0) < 1e-6, f"A/V origins diverge: {v0} vs {a0}"

    # PTS/DTS offset (B-frame reordering) must survive on the video track.
    assert any(p.cto != 0 for p in published["video"]), \
        "video composition offsets (B frames) were lost"


def verify_generation_coverage(session: CaptureSession, output: Path) -> dict:
    """Report, per epoch, retained vs missing source sequences per track."""
    progress = __import__("json").loads(
        (output / "progress.json").read_text())
    coverage = {}
    for epoch in session.epochs:
        coverage[epoch.epoch] = {
            track: {
                "retained": [stored.seq
                             for stored in ordered_fragments(epoch, track)],
                "missing": missing_numbers(epoch, track),
            }
            for track in ("video", "audio")
        }
    progress_epochs = {e["epoch"]: e for e in progress["epochs"]}
    for epoch_name, tracks in coverage.items():
        for track, info in tracks.items():
            assert info["missing"] == \
                progress_epochs[epoch_name]["tracks"][track]["missing"]
    return coverage
