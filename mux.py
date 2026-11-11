"""Deterministic two-track HLS fMP4 packaging.

The capture side delivers independent H.264 and AAC single-track fragments.
This module turns the accepted fragments into a two-track fragmented MP4
initialisation segment plus GOP-aligned HLS media segments.  No compressed
sample is ever decoded or re-encoded: payload bytes are copied as received, the
original decode order is kept and every sample's composition offset is carried
over unchanged.

GOP based segmentation makes the published bytes independent of when (or in
which order) the log arrived, and makes a replay after a crash reproduce the
same segments byte for byte.
"""

import hashlib
import os
from bisect import bisect_right
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mp4 import (
    make_segment,
    merge_init,
    parse_fragment,
    parse_init,
    sample_is_key,
)

INIT_BASENAME = "init.mp4"
SEGMENT_PATTERN = "segment-%03d.m4s"


class MuxError(ValueError):
    """Raised when a capture cannot be turned into a decodable stream."""


def ffmpeg_executable() -> str:
    """Kept for compatibility with the original fixture/regression setup."""
    return os.environ.get("FFMPEG", "ffmpeg")


def _round_half_up(value: Fraction) -> int:
    if value >= 0:
        return int(value + Fraction(1, 2))
    return -int(-value + Fraction(1, 2))


def _sample_end(sample: dict) -> int:
    return sample["pts"] + sample["duration"]


class _Track:
    """Accepted fragments of one track, in sequence order."""

    def __init__(self, name: str, model: dict, info: dict):
        self.name = name
        self.model = model
        self.info = info
        self.timescale = info["timescale"]
        declared = model.get("timescale")
        if declared is not None and declared != self.timescale:
            raise MuxError(
                "track %s declares timescale %s but its init uses %s"
                % (name, declared, self.timescale)
            )
        self.origin = model["clock_origin"]

    def fragments(self) -> List[dict]:
        defaults = {self.info["track_id"]: self.info.get("trex") or {}}
        result = []
        next_dts: Optional[int] = None
        sealed_last = self.model.get("last")
        for seq in sorted(self.model["candidates"]):
            if sealed_last is not None and seq > sealed_last:
                continue
            entry = self.model["candidates"][seq]
            try:
                parsed = parse_fragment(entry["path"].read_bytes(), defaults)
            except Exception as error:
                raise MuxError(
                    "cannot read fragment %s/%s/%d: %s"
                    % (self.model.get("epoch"), self.name, seq, error)
                ) from error
            tracks = parsed["tracks"]
            samples = tracks.get(self.info["track_id"])
            if samples is None:
                # A single-track fragment: take the only track it has.
                if len(tracks) != 1:
                    raise MuxError(
                        "fragment %s/%s/%d has no track %d"
                        % (self.model.get("epoch"), self.name, seq,
                           self.info["track_id"])
                    )
                samples = next(iter(tracks.values()))
            if next_dts is not None:
                for sample in samples:
                    if "dts" not in sample:
                        sample["dts"] = next_dts
                        sample["pts"] = next_dts + sample["cto"]
                    next_dts = sample["dts"] + sample["duration"]
            elif samples and "dts" not in samples[0]:
                # No tfdt anywhere: start at the clock origin.
                running = 0
                for sample in samples:
                    sample["dts"] = running
                    sample["pts"] = running + sample["cto"]
                    running += sample["duration"]
                next_dts = running
            elif samples:
                next_dts = samples[-1]["dts"] + samples[-1]["duration"]
            for sample in samples:
                sample["key"] = sample_is_key(sample["flags"])
            result.append({"seq": seq, "samples": samples})
        return result


def _gops(fragments: List[dict]) -> List[dict]:
    """Group video samples into closed GOPs starting at a keyframe.

    Samples before the first keyframe of a contiguous run are discarded:
    without their reference frame they are not independently decodable.  This
    is what makes a permanent hole recover at the next keyframe.
    """
    gops: List[dict] = []
    current: Optional[dict] = None
    previous_seq: Optional[int] = None
    for fragment in fragments:
        seq = fragment["seq"]
        contiguous = previous_seq is not None and seq == previous_seq + 1
        if not contiguous:
            if current is not None:
                gops.append(current)
                current = None
            gap_before = bool(gops)
        else:
            gap_before = False
        if current is not None and gap_before:
            gops.append(current)
            current = None
        for sample in fragment["samples"]:
            if sample["key"]:
                if current is not None:
                    gops.append(current)
                current = {
                    "samples": [sample],
                    "gap_before": gap_before,
                }
                gap_before = False
            elif current is not None:
                current["samples"].append(sample)
            # else: leading non-key samples of a run are not decodable.
        previous_seq = seq
    if current is not None:
        gops.append(current)

    for index, gop in enumerate(gops):
        gop["index"] = index
        gop["start"] = min(sample["pts"] for sample in gop["samples"])
        gop["end"] = max(_sample_end(sample) for sample in gop["samples"])

    for index, gop in enumerate(gops):
        next_gop = gops[index + 1] if index + 1 < len(gops) else None
        if (next_gop is not None and not next_gop.get("gap_before")
                and next_gop["start"] > gop["start"]):
            gop["playlist_end"] = next_gop["start"]
        else:
            gop["playlist_end"] = gop["end"]
        if gop["playlist_end"] <= gop["start"]:
            gop["playlist_end"] = gop["end"]
        # Audio is partitioned by the GOP's own presentation end.  This keeps
        # the bytes of an already published segment independent of data that
        # arrives later; without gaps the next GOP starts exactly here.
        gop["audio_end"] = gop["end"]
    return gops


def _shift(samples: List[dict], origin: int, timescale: int,
           base: Fraction) -> None:
    delta = _round_half_up(base * timescale) - origin
    if samples:
        # A sub-tick rounding difference must never produce a negative
        # baseMediaDecodeTime (tfdt is unsigned).
        delta = max(delta, -min(sample["dts"] for sample in samples))
    for sample in samples:
        sample["dts"] += delta
        sample["pts"] += delta


def _assign_audio(gops: List[dict], audio: List[dict],
                  video_timescale: int, audio_timescale: int) -> None:
    """Attach audio samples to the video GOP whose presentation interval they
    overlap.  Audio that falls into a permanently missing video region is left
    unassigned and therefore not published; the next GOP starts aligned."""
    for gop in gops:
        gop["audio"] = []
    if not gops:
        return
    starts = [Fraction(gop["start"], video_timescale) for gop in gops]
    boundaries = [Fraction(gop["audio_end"], video_timescale) for gop in gops]
    first_start = starts[0]
    for sample in audio:
        # Partition by presentation start, not midpoint: a frame that starts
        # inside the GOP is published with it even if it extends slightly
        # beyond the GOP's end (AAC frames rarely end exactly on a video
        # boundary).
        point = Fraction(sample["pts"], audio_timescale)
        position = bisect_right(starts, point) - 1
        if position < 0:
            # A lead-in frame that still overlaps the first GOP.
            if Fraction(_sample_end(sample), audio_timescale) > first_start:
                gops[0]["audio"].append(sample)
            continue
        if point < boundaries[position]:
            gops[position]["audio"].append(sample)
        # Otherwise the sample belongs to the hole and is dropped.


def _prepare_epoch(epoch: dict) -> dict:
    names = epoch["tracks"]
    if "video" not in names:
        raise MuxError("epoch %r has no video track" % epoch["epoch"])
    video_model = epoch["tracks"]["video"]
    audio_model = epoch["tracks"].get("audio")

    try:
        video_init = parse_init(video_model["init"].read_bytes())
        audio_init = parse_init(audio_model["init"].read_bytes()) \
            if audio_model is not None else None
    except Exception as error:
        raise MuxError("invalid init segment for epoch %r: %s"
                       % (epoch["epoch"], error)) from error

    video = _Track("video", video_model, video_init)
    video_fragments = video.fragments()
    audio_track = _Track("audio", audio_model, audio_init) \
        if audio_model is not None and audio_init is not None else None
    audio_samples = []
    if audio_track is not None:
        for fragment in audio_track.fragments():
            audio_samples.extend(fragment["samples"])

    raw_video = [sample for fragment in video_fragments
                 for sample in fragment["samples"]]
    if not raw_video:
        raise MuxError(
            "epoch %r has no independently decodable video"
            % epoch["epoch"]
        )

    all_samples = raw_video + audio_samples
    if any(sample["dts"] < 0 for sample in all_samples):
        raise MuxError("epoch %r has negative decode timestamps"
                       % epoch["epoch"])

    video_start = min(Fraction(sample["pts"] - video.origin,
                               video.timescale) for sample in raw_video)
    starts = [video_start]
    min_dts = min(Fraction(sample["dts"] - video.origin, video.timescale)
                  for sample in raw_video)
    if audio_samples:
        starts.append(min(Fraction(sample["pts"] - audio_track.origin,
                                   audio_track.timescale)
                          for sample in audio_samples))
        min_dts = min(min_dts, min(
            Fraction(sample["dts"] - audio_track.origin,
                     audio_track.timescale) for sample in audio_samples))
    normalized_start = min(starts)

    return {
        "epoch": epoch,
        "video": video,
        "video_fragments": video_fragments,
        "audio": audio_track,
        "audio_samples": audio_samples,
        "normalized_start": normalized_start,
        "min_dts": min_dts,
        "video_init": video_init,
        "audio_init": audio_init,
    }


def plan_session(session: dict) -> dict:
    """Build the complete output in memory.

    The returned mapping contains the init/media bytes and playlist text so
    that a caller can inspect or publish them.  Everything is a pure function
    of the log's accepted fragments: arrival order and duplicates have no
    influence on the result.
    """
    epochs = session.get("epochs")
    if not epochs:
        raise MuxError("capture has no recording generation")
    for epoch in epochs:
        if not epoch.get("sealed"):
            raise MuxError("epoch %r is not sealed" % epoch["epoch"])

    prepared = [_prepare_epoch(epoch) for epoch in epochs]

    init_names: Dict[str, str] = {}
    init_bytes: Dict[str, bytes] = {}
    segments: List[dict] = []
    epoch_info: List[dict] = []

    previous_end = Fraction(0)
    previous_init_key: Optional[str] = None

    def init_name(key: str, data: bytes) -> str:
        if key not in init_names:
            name = INIT_BASENAME if not init_names \
                else "init-%d.mp4" % (len(init_names) + 1)
            init_names[key] = name
            init_bytes[name] = data
        return init_names[key]

    for epoch_index, item in enumerate(prepared):
        epoch = item["epoch"]
        video = item["video"]
        audio = item["audio"]
        base = Fraction(0)
        if epoch_index == 0:
            if item["min_dts"] < 0:
                base = -item["min_dts"]
        else:
            if item["normalized_start"] < previous_end:
                base = previous_end - item["normalized_start"]
            if item["min_dts"] + base < 0:
                base = -item["min_dts"]
        _shift([sample for fragment in item["video_fragments"]
                for sample in fragment["samples"]],
               video.origin, video.timescale, base)
        if audio is not None:
            _shift(item["audio_samples"], audio.origin, audio.timescale, base)

        if item["audio_init"] is None:
            raise MuxError("epoch %r has no audio init" % epoch["epoch"])
        try:
            merged_init = merge_init(item["video_init"], item["audio_init"])
        except Exception as error:
            raise MuxError("cannot build init for epoch %r: %s"
                           % (epoch["epoch"], error)) from error
        init_key = hashlib.sha256(merged_init).hexdigest()
        name = init_name(init_key, merged_init)
        changed = previous_init_key is not None and init_key != previous_init_key
        discontinuity = epoch_index > 0 and (
            changed or base > 0 or item["normalized_start"] < previous_end
        )

        gops = _gops(item["video_fragments"])
        if not gops:
            raise MuxError("epoch %r has no decodable GOP" % epoch["epoch"])
        _assign_audio(gops, item["audio_samples"],
                      video.timescale,
                      audio.timescale if audio is not None else 0)

        epoch_segment_count = 0
        for gop in gops:
            video_samples = [
                {
                    "dts": sample["dts"],
                    "duration": sample["duration"],
                    "size": sample["size"],
                    "flags": sample["flags"],
                    "cto": sample["cto"],
                    "data": sample["data"],
                }
                for sample in gop["samples"]
            ]
            audio_samples = [
                {
                    "dts": sample["dts"],
                    "duration": sample["duration"],
                    "size": sample["size"],
                    "flags": sample["flags"],
                    "cto": sample["cto"],
                    "data": sample["data"],
                }
                for sample in sorted(gop["audio"],
                                     key=lambda sample: sample["dts"])
            ]
            segment_index = len(segments)
            payload = make_segment(
                segment_index + 1,
                [(1, video_samples), (2, audio_samples)] if audio_samples
                else [(1, video_samples)],
            )
            duration = Fraction(gop["playlist_end"] - gop["start"],
                                video.timescale)
            segments.append({
                "index": segment_index,
                "name": SEGMENT_PATTERN % segment_index,
                "epoch": epoch["epoch"],
                "init": name,
                "discontinuity": bool(discontinuity and epoch_segment_count == 0),
                "duration": duration,
                "start": Fraction(gop["start"], video.timescale),
                "data": payload,
                "video_samples": len(video_samples),
                "audio_samples": len(audio_samples),
            })
            epoch_segment_count += 1

        # Where the generation actually ends in the published timeline.
        # Unpublished samples (leading undecodable GOP data and audio in a
        # permanent hole) must not extend the next generation's base.
        published_end = Fraction(0)
        for gop in gops:
            published_end = max(published_end,
                                Fraction(gop["playlist_end"],
                                         video.timescale))
            if audio is not None:
                for sample in gop["audio"]:
                    published_end = max(
                        published_end,
                        Fraction(_sample_end(sample), audio.timescale),
                    )
        previous_end = max(previous_end, published_end)
        previous_init_key = init_key
        epoch_info.append({
            "epoch": epoch["epoch"],
            "init": name,
            "segments": epoch_segment_count,
            "discontinuity": bool(discontinuity),
            "base_seconds": base,
            "retained_video": sum(
                len(fragment["samples"]) for fragment in item["video_fragments"]
            ),
            "retained_audio": len(item["audio_samples"]),
            "missing": {track: sorted(model["missing"])
                        for track, model in epoch["tracks"].items()},
        })

    if not segments:
        raise MuxError("capture produced no segments")

    durations = [segment["duration"] for segment in segments]
    target = max(1, int(-(-max(durations).numerator // max(durations).denominator)))
    playlist_lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-TARGETDURATION:%d" % target,
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    active_init: Optional[str] = None
    for segment in segments:
        if segment["discontinuity"]:
            playlist_lines.append("#EXT-X-DISCONTINUITY")
        if segment["init"] != active_init:
            playlist_lines.append('#EXT-X-MAP:URI="%s"' % segment["init"])
            active_init = segment["init"]
        playlist_lines.append("#EXTINF:%.3f," % float(segment["duration"]))
        playlist_lines.append(segment["name"])
    playlist_lines.append("#EXT-X-ENDLIST")
    playlist = "\n".join(playlist_lines) + "\n"

    return {
        "inits": init_bytes,
        "segments": segments,
        "playlist": playlist,
        "epochs": epoch_info,
        "target_duration": target,
    }


def make_playlist(session: dict, stage: Path) -> None:
    """Write the deterministic init segment, media segments and playlist."""
    stage = Path(stage)
    stage.mkdir(parents=True, exist_ok=True)
    plan = plan_session(session)
    for name, data in plan["inits"].items():
        (stage / name).write_bytes(data)
    for segment in plan["segments"]:
        (stage / segment["name"]).write_bytes(segment["data"])
    (stage / "index.m3u8").write_text(plan["playlist"], encoding="utf-8")
