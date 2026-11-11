"""Deterministic dual-track fMP4 packaging and HLS EVENT publication.

The muxer never re-encodes: every retained compressed sample is copied with
its original bytes.  Per-track timestamps stay in the capture timescales and
are mapped onto the output timeline by a single constant shift per
``(epoch, track)``, so the PTS/DTS relationship (including B-frame reordering
and non-integer frame rates) and the audio/video alignment defined by the two
clock origins are preserved.

Recovery from a permanent video gap resumes at the next fragment whose first
sample is independently decodable; audio samples belonging to the dropped
interval are discarded and an ``EXT-X-DISCONTINUITY`` marker is emitted.
A capture restart that zeroes timestamps starts a new epoch with its own
shift (and its own init section when the codec configuration changes).
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fmp4 import (
    Fragment,
    build_dual_init,
    build_segment,
    read_fragment,
    trex_defaults,
)
from ingest import CaptureSession, EpochState, missing_numbers, ordered_fragments

VIDEO = "video"
AUDIO = "audio"
SEGMENT_PREFIX = "segment-"
INIT_PREFIX = "init-"
PLAYLIST_NAME = "index.m3u8"


def ffmpeg_executable() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


# ---------------------------------------------------------------------------
# Plan construction (pure function of the capture log; reused by verifier)
# ---------------------------------------------------------------------------

@dataclass
class PlannedSample:
    payload: bytes
    dts: int                        # output timeline, track ticks
    cto: int
    duration: int
    is_sync: bool
    sample_flags: int
    source_seq: int
    source_index: int               # sample index inside the source fragment
    digest: str


@dataclass
class PlannedSegment:
    number: int                     # one-based media sequence
    epoch: str
    source_seq: int
    init_name: str
    discontinuity: bool
    video_base: int
    video_samples: List[PlannedSample]
    video_default_duration: int
    audio_base: Optional[int]
    audio_samples: List[PlannedSample]
    audio_default_duration: int
    extinf: float


@dataclass
class MuxPlan:
    epochs: List[str]
    inits: Dict[str, bytes]         # init file name -> bytes
    segments: List[PlannedSegment]
    target_duration: int
    retained: Dict[str, List[Tuple[int, int, str]]] = field(default_factory=dict)
    video_missing: Dict[str, List[int]] = field(default_factory=dict)
    discontinuities: List[int] = field(default_factory=list)


def _parse_epoch_fragments(epoch: EpochState) -> Dict[str, List[Tuple[int, Fragment]]]:
    parsed: Dict[str, List[Tuple[int, Fragment]]] = {}
    for track in (VIDEO, AUDIO):
        spec = epoch.tracks[track]
        defaults = trex_defaults(spec.init_bytes)
        trex = defaults.get(1) or (next(iter(defaults.values())) if defaults else None)
        rows = []
        for stored in ordered_fragments(epoch, track):
            data = stored.data if stored.data is not None else stored.path.read_bytes()
            fragment = read_fragment(data, track == VIDEO, trex)
            rows.append((stored.seq, fragment))
        parsed[track] = rows
    return parsed


def _epoch_shift(epoch: EpochState, track: str,
                 parsed: List[Tuple[int, Fragment]],
                 previous_end: Optional[int]) -> int:
    """Constant shift mapping source ticks of this epoch onto output ticks."""
    first_base = parsed[0][1].base_media_decode_time if parsed else 0
    if previous_end is None:
        # First epoch: the two clock origins describe the same instant.
        return int(epoch.tracks[track].clock_origin) - first_base
    return previous_end - first_base


def _fragment_interval(fragment: Fragment, shift: int) -> Tuple[int, int]:
    start = fragment.base_media_decode_time + shift
    return start, start + fragment.duration


def build_plan(session: CaptureSession) -> MuxPlan:
    init_cache: Dict[str, str] = {}
    inits: Dict[str, bytes] = {}
    segments: List[PlannedSegment] = []
    retained: Dict[str, List[Tuple[int, int, str]]] = {}
    video_missing: Dict[str, List[int]] = {}
    discontinuities: List[int] = []

    # Output timeline cursor per track (last emitted sample end, output ticks).
    ends: Dict[str, Optional[int]] = {VIDEO: None, AUDIO: None}
    previous_init_name: Optional[str] = None
    segment_number = 0
    target_duration = 1

    for epoch_index, epoch in enumerate(session.epochs):
        parsed = _parse_epoch_fragments(epoch)
        shifts = {
            track: _epoch_shift(epoch, track, parsed[track], ends[track])
            for track in (VIDEO, AUDIO)
        }
        spec = epoch.tracks
        dual_init = build_dual_init(spec[VIDEO].init_bytes,
                                    spec[AUDIO].init_bytes)
        init_hash = hashlib.sha256(dual_init).hexdigest()[:16]
        init_name = init_cache.get(init_hash)
        if init_name is None:
            init_name = f"{INIT_PREFIX}{len(inits) + 1:02d}.mp4"
            init_cache[init_hash] = init_name
            inits[init_name] = dual_init

        missing = set(missing_numbers(epoch, VIDEO))
        video_missing[epoch.epoch] = sorted(missing)
        video_rows = parsed[VIDEO]
        audio_rows = parsed[AUDIO]

        # Flatten retained audio samples and remember source provenance.
        audio_flat: List[PlannedSample] = []
        for source_seq, fragment in audio_rows:
            for index, sample in enumerate(fragment.samples):
                dts = fragment.base_media_decode_time + sum(
                    s.duration for s in fragment.samples[:index]) + shifts[AUDIO]
                audio_flat.append(PlannedSample(
                    payload=sample.payload, dts=dts, cto=0,
                    duration=sample.duration, is_sync=True,
                    sample_flags=sample.sample_flags,
                    source_seq=source_seq, source_index=index,
                    digest=sample.digest))
        # Audio DTS is monotonic; remember which samples have already been
        # attached to an earlier segment so each retained sample is used once,
        # but select per video interval rather than draining a shared cursor.
        audio_next = 0
        v_scale = epoch.tracks[VIDEO].timescale
        a_scale = epoch.tracks[AUDIO].timescale

        def to_audio_ticks(video_ticks: int) -> int:
            # Compare the two tracks on a common wall-clock axis and express
            # the video boundary in audio track ticks.
            return round(video_ticks * a_scale / v_scale)

        previous_video_end: Optional[int] = None
        previous_audio_end: Optional[int] = None
        prev_audio_seq: Optional[int] = None
        prev_epoch: Optional[str] = segments[-1].epoch if segments else None
        for source_seq, fragment in video_rows:
            interval_start, interval_end = _fragment_interval(
                fragment, shifts[VIDEO])
            # Audio equivalents of the video interval, in audio ticks.
            audio_window_start = to_audio_ticks(interval_start)
            audio_window_end = to_audio_ticks(interval_end)

            # Permanent-gap recovery: only fragments starting on a sync sample
            # can begin a new independently decodable run.
            if not fragment.starts_with_sync:
                retained.setdefault(epoch.epoch, []).append(
                    (source_seq, 0, "dropped-non-sync"))
                previous_video_end = interval_end
                continue

            video_samples: List[PlannedSample] = []
            running = fragment.base_media_decode_time
            for index, sample in enumerate(fragment.samples):
                dts = running + shifts[VIDEO]
                video_samples.append(PlannedSample(
                    payload=sample.payload, dts=dts, cto=sample.cto,
                    duration=sample.duration, is_sync=sample.is_sync,
                    sample_flags=sample.sample_flags,
                    source_seq=source_seq, source_index=index,
                    digest=sample.digest))
                running += sample.duration
            retained.setdefault(epoch.epoch, []).append(
                (source_seq, len(video_samples), "retained"))

            # Select the retained audio samples whose start falls inside this
            # video interval.  Samples before the interval (they belong to a
            # dropped gap) are advanced past once and never reused; samples at
            # or after the window start are matched even across a gap.  Audio
            # and video boundaries are compared on their common time axis.
            # A one-AAC-frame tolerance keeps a trailing partial frame that
            # only differs from the video end by encoder framing rounding.
            audio_frame_ticks = 1024
            while (audio_next < len(audio_flat)
                   and audio_flat[audio_next].dts < audio_window_start):
                audio_next += 1
            audio_samples: List[PlannedSample] = []
            probe = audio_next
            while probe < len(audio_flat) \
                    and audio_flat[probe].dts < audio_window_end + \
                    audio_frame_ticks:
                audio_samples.append(audio_flat[probe])
                probe += 1
            # Only consume the matched samples; audio_next jumps past them so
            # a later interval never re-attaches the same audio sample.
            if audio_samples:
                audio_next = probe

            video_gap = (previous_video_end is not None
                         and previous_video_end != interval_start)
            epoch_break = prev_epoch is not None and prev_epoch != epoch.epoch
            init_switch = previous_init_name is not None and \
                previous_init_name != init_name
            if audio_samples:
                first_audio_seq = audio_samples[0].source_seq
                # A discontinuity only when audio source seqs skip a number
                # (a permanent audio gap); moving to the next contiguous source
                # fragment across a segment boundary is normal.
                audio_gap = (
                    prev_audio_seq is not None
                    and first_audio_seq > prev_audio_seq + 1)
            else:
                audio_gap = False
            discontinuity = bool(
                video_gap or audio_gap or epoch_break or init_switch
                or (source_seq - 1 in missing))

            segment_number += 1
            extinf = fragment.duration / spec[VIDEO].timescale
            target_duration = max(target_duration, int(extinf + 0.999999))
            segment = PlannedSegment(
                number=segment_number, epoch=epoch.epoch,
                source_seq=source_seq, init_name=init_name,
                discontinuity=discontinuity,
                video_base=interval_start, video_samples=video_samples,
                video_default_duration=fragment.default_duration,
                audio_base=(audio_samples[0].dts if audio_samples else None),
                audio_samples=audio_samples,
                audio_default_duration=(audio_rows[0][1].default_duration
                                        if audio_rows else 1024),
                extinf=extinf)
            segments.append(segment)
            if discontinuity:
                discontinuities.append(segment_number)

            previous_video_end = interval_end
            if audio_samples:
                previous_audio_end = audio_samples[-1].dts + \
                    audio_samples[-1].duration
                prev_audio_seq = audio_samples[-1].source_seq
                ends[AUDIO] = previous_audio_end
            ends[VIDEO] = interval_end
            previous_init_name = init_name
            prev_epoch = epoch.epoch

    if not segments:
        raise ValueError("capture contains no decodable video fragments")
    return MuxPlan(
        epochs=[state.epoch for state in session.epochs],
        inits=inits, segments=segments,
        target_duration=target_duration, retained=retained,
        video_missing=video_missing, discontinuities=discontinuities)


# ---------------------------------------------------------------------------
# Publication (atomic, crash-safe ordering)
# ---------------------------------------------------------------------------

# Crash injection points used by the acceptance harness.  The process exits
# immediately so the on-disk state is exactly the state of that instant.
CRASH_AFTER_SEGMENT = "RECORDER_CRASH_AFTER_SEGMENT"
CRASH_BEFORE_PROGRESS = "RECORDER_CRASH_BEFORE_PROGRESS"


def _maybe_crash(env_var: str) -> None:
    if os.environ.get(env_var):
        os._exit(9)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _segment_bytes(segment: PlannedSegment) -> bytes:
    return build_segment(
        sequence=segment.number,
        video_base_time=segment.video_base,
        video_samples=segment.video_samples,
        video_default_duration=segment.video_default_duration,
        audio_base_time=segment.audio_base,
        audio_samples=segment.audio_samples,
        audio_default_duration=segment.audio_default_duration)


def _segment_name(number: int) -> str:
    return f"{SEGMENT_PREFIX}{number:03d}.m4s"


def render_playlist(plan: MuxPlan) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:9",
        f"#EXT-X-TARGETDURATION:{plan.target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    current_init = None
    for segment in plan.segments:
        if segment.init_name != current_init:
            lines.append(f'#EXT-X-MAP:URI="{segment.init_name}"')
            current_init = segment.init_name
        if segment.discontinuity:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{segment.extinf:.6f},")
        lines.append(_segment_name(segment.number))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def cleanup_stale(output: Path) -> None:
    """Remove leftovers from an interrupted previous run."""
    if not output.exists():
        return
    for path in output.iterdir():
        if path.name.endswith(".tmp"):
            path.unlink()


def publish_plan(plan: MuxPlan, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cleanup_stale(output)

    # Init sections are content-addressed: reruns always produce the same name.
    for name, data in plan.inits.items():
        target = output / name
        if not target.exists() or hashlib.sha256(target.read_bytes()).digest() != \
                hashlib.sha256(data).digest():
            _atomic_write(target, data)

    # Segments first, playlist replacement second, progress last.  At every
    # instant the playlist references only files already on disk.
    for segment in plan.segments:
        _atomic_write(output / _segment_name(segment.number),
                      _segment_bytes(segment))
        _maybe_crash(CRASH_AFTER_SEGMENT)

    _atomic_write(output / PLAYLIST_NAME, render_playlist(plan).encode("utf-8"))
    _maybe_crash(CRASH_BEFORE_PROGRESS)


def make_playlist(session: CaptureSession, stage: Path) -> MuxPlan:
    """Backwards-compatible entry point: build the plan into a stage dir."""
    plan = build_plan(session)
    stage.mkdir(parents=True, exist_ok=True)
    for name, data in plan.inits.items():
        (stage / name).write_bytes(data)
    for segment in plan.segments:
        (stage / _segment_name(segment.number)).write_bytes(
            _segment_bytes(segment))
    (stage / PLAYLIST_NAME).write_text(render_playlist(plan), encoding="utf-8")
    return plan
