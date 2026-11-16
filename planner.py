#!/usr/bin/env python3
"""Plan gapless playback of decoded, pre-encoded audio frames.

A track is a sequence of decoded frames on a shared sample clock.  Each frame
occupies ``samples`` consecutive decoded-track positions and may declare an
encoder ``priming`` and a trailing ``padding``.  Those two values are markers
on the decoded sample axis, not properties that necessarily fit inside one
frame: an unusually long final-frame padding reaches back into the preceding
frame, and a long first-frame priming reaches forward into the next one.  The
planner subtracts the union of all marker intervals from the requested
inclusive playable interval and emits frame-local half-open slices.

Playable samples are packed back to back in the concatenated output, so the
global sample clock has no holes at track joins or around empty tracks.  The
emitted ``mapping`` is a piecewise-linear, invertible bridge between that
global clock and ``track/frame/local sample`` coordinates.
"""

import json
import sys


class PlanError(ValueError):
    """Metadata cannot describe the decoded frames of a track."""


_SAMPLE_KEYS = ("samples", "sample_count", "count", "length")
_PRIMING_KEYS = ("priming", "encoder_priming", "lead_padding", "delay")
_PADDING_KEYS = ("padding", "trailing_padding", "tail_padding", "end_padding")
_FRAMES_KEYS = ("frames", "decoded_frames")
_PLAYABLE_KEYS = ("playable", "playable_range", "playable_interval", "interval")


def _lookup(mapping, names):
    """Return ``(found, value)`` for the first present alias in *names*."""
    if not isinstance(mapping, dict):
        return False, None
    for name in names:
        if name in mapping:
            return True, mapping[name]
    return False, None


def _as_int(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlanError("%s must be an integer" % label)
    if minimum is not None and value < minimum:
        raise PlanError("%s must be >= %d" % (label, minimum))
    return value


def _read_trim(raw, names, label, default=0):
    found, value = _lookup(raw, names)
    if not found:
        return default
    return _as_int(value, label, 0)


def _read_playable(track):
    found, value = _lookup(track, _PLAYABLE_KEYS)
    if not found:
        raise PlanError("missing playable interval")

    if isinstance(value, dict):
        s_found, start = _lookup(value, ("start", "begin", "lo"))
        e_found, end = _lookup(value, ("end", "stop", "hi"))
        if not s_found or not e_found:
            raise PlanError("playable interval must contain start and end")
    elif isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise PlanError("playable interval must contain two endpoints")
        start, end = value
    else:
        raise PlanError("playable interval must be [start, end]")

    return _as_int(start, "playable start"), _as_int(end, "playable end")


def _track_contradictions(track, parsed, shared_rate=None):
    """Report metadata that contradicts the decoded frames of one track.

    These are reports, not rejections: the frames are still describable, so
    the track keeps playing with the union-of-markers interpretation.
    """
    warnings = []

    if "sample_rate" in track and shared_rate is not None:
        rate = track.get("sample_rate")
        if rate != shared_rate:
            warnings.append(
                "track sample_rate %r differs from the shared sample_rate %r; "
                "frames are planned on the shared clock" % (rate, shared_rate)
            )

    for frame in parsed["frames"]:
        index = frame["index"]
        samples = frame["samples"]
        priming = frame["priming"]
        padding = frame["padding"]
        if priming > samples:
            warnings.append(
                "frame %d priming %d exceeds its %d decoded samples; the marker "
                "continues into the following decoded samples"
                % (index, priming, samples)
            )
        if padding > samples:
            warnings.append(
                "frame %d padding %d exceeds its %d decoded samples; the marker "
                "reaches back into the preceding decoded samples"
                % (index, padding, samples)
            )
        if priming and padding and priming + padding >= samples:
            warnings.append(
                "frame %d markers (%d priming + %d padding) remove all of its "
                "%d decoded samples" % (index, priming, padding, samples)
            )

    # Overlapping marker covers are only possible when a marker outgrows its
    # own frame.  The union is trimmed once, which is what prevents both a
    # duplicate removal and a gap between the two markers.
    cuts = _merge(_marker_cuts(parsed["frames"], parsed["total"]))
    for i, (a, b) in enumerate(cuts):
        if i + 1 < len(cuts) and cuts[i + 1][0] < b:
            c, d = cuts[i + 1]
            warnings.append(
                "overlapping trim markers [%d, %d) and [%d, %d); their union is "
                "trimmed once" % (a, b, c, d)
            )

    if parsed["end"] < parsed["start"]:
        warnings.append(
            "playable interval [%d, %d] is empty" % (parsed["start"], parsed["end"])
        )
    elif parsed["total"] == 0:
        warnings.append("track has no decoded samples")

    return warnings


def _parse_track(track):
    if not isinstance(track, dict):
        raise PlanError("track must be a JSON object")

    found, raw_frames = _lookup(track, _FRAMES_KEYS)
    if not found:
        raise PlanError("missing frames")
    if not isinstance(raw_frames, (list, tuple)):
        raise PlanError("frames must be an array")

    # Track-level markers are accepted as a convenience and attached to the
    # edge frame that does not carry its own marker.
    priming_found, priming_raw = _lookup(track, _PRIMING_KEYS)
    padding_found, padding_raw = _lookup(track, _PADDING_KEYS)
    track_priming = (
        _as_int(priming_raw, "track priming", 0) if priming_found else None
    )
    track_padding = (
        _as_int(padding_raw, "track padding", 0) if padding_found else None
    )

    frames = []
    offset = 0
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, dict):
            raise PlanError("frame %d must be a JSON object" % index)

        s_found, samples_raw = _lookup(raw, _SAMPLE_KEYS)
        if not s_found:
            raise PlanError("frame %d is missing sample count" % index)
        samples = _as_int(samples_raw, "frame %d samples" % index, 0)

        p_found, _ = _lookup(raw, _PRIMING_KEYS)
        if p_found:
            priming = _read_trim(raw, _PRIMING_KEYS, "frame %d priming" % index)
        elif index == 0 and track_priming is not None:
            priming = track_priming
        else:
            priming = 0

        d_found, _ = _lookup(raw, _PADDING_KEYS)
        if d_found:
            padding = _read_trim(raw, _PADDING_KEYS, "frame %d padding" % index)
        elif index == len(raw_frames) - 1 and track_padding is not None:
            padding = track_padding
        else:
            padding = 0

        frames.append(
            {
                "index": index,
                "offset": offset,
                "samples": samples,
                "priming": priming,
                "padding": padding,
            }
        )
        offset += samples

    start, end = _read_playable(track)
    total = offset

    # Both endpoints are inclusive sample positions, extended by the two
    # boundary positions so every empty range remains representable:
    # ``start`` may be ``total`` (just after the last sample) and ``end`` may
    # be -1 (just before the first).  Anything further outside points past the
    # decoded track and is contradictory metadata.  ``end < start`` inside
    # this region is the documented way to request zero playable samples.
    if start < 0 or start > total:
        raise PlanError(
            "playable start %d outside decoded samples [0, %d]" % (start, total)
        )
    if end < -1 or end > total - 1:
        raise PlanError(
            "playable end %d outside decoded samples [-1, %d]" % (end, total - 1)
        )

    return {
        "id": track.get("id"),
        "frames": frames,
        "total": total,
        "start": start,
        "end": end,
    }


def _clip(a, b, total):
    a = max(0, min(total, a))
    b = max(0, min(total, b))
    return (a, b) if a < b else None


def _merge(intervals):
    merged = []
    for a, b in sorted(intervals):
        if a >= b:
            continue
        if merged and a <= merged[-1][1]:
            if b > merged[-1][1]:
                merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def _subtract(pieces, cuts):
    """Subtract merged, sorted half-open *cuts* from *pieces*.

    Both inputs are sorted and disjoint, so a single sweep is enough; this
    keeps a track with tens of thousands of trimmed frames linear instead of
    quadratic.
    """
    result = []
    cut_index = 0
    for lo, hi in pieces:
        cursor = lo
        while cut_index < len(cuts) and cuts[cut_index][1] <= cursor:
            cut_index += 1
        probe = cut_index
        while probe < len(cuts) and cuts[probe][0] < hi:
            a, b = cuts[probe]
            if a > cursor:
                result.append((cursor, min(a, hi)))
            cursor = max(cursor, b)
            if cursor >= hi:
                break
            probe += 1
        if cursor < hi:
            result.append((cursor, hi))
    return result


def _marker_cuts(frames, total):
    cuts = []
    for frame in frames:
        offset = frame["offset"]
        samples = frame["samples"]

        # Priming runs forward from the frame's first decoded sample.
        if frame["priming"]:
            cut = _clip(offset, offset + frame["priming"], total)
            if cut:
                cuts.append(cut)

        # Padding runs backward from the frame's last decoded sample.  When it
        # is longer than the frame itself it deliberately reaches into the
        # preceding decoded frame; clipping only happens at track edges.
        if frame["padding"]:
            cut = _clip(offset + samples - frame["padding"], offset + samples, total)
            if cut:
                cuts.append(cut)
    return cuts


def _free_pieces(parsed):
    total = parsed["total"]
    if total == 0 or parsed["end"] < parsed["start"]:
        return []
    cuts = _marker_cuts(parsed["frames"], total)
    request = [(parsed["start"], parsed["end"] + 1)]
    return _subtract(request, _merge(cuts))


def _slices(parsed, pieces):
    """Convert decoded free intervals into frame-local half-open slices."""
    frames = parsed["frames"]
    result = []
    frame_index = 0
    for lo, hi in pieces:
        while (
            frame_index < len(frames)
            and frames[frame_index]["offset"] + frames[frame_index]["samples"] <= lo
        ):
            frame_index += 1

        index = frame_index
        while index < len(frames):
            frame = frames[index]
            if frame["offset"] >= hi:
                break
            a = max(lo, frame["offset"])
            b = min(hi, frame["offset"] + frame["samples"])
            if a < b:
                local_start = a - frame["offset"]
                local_end = b - frame["offset"]
                if result and result[-1]["frame"] == index and result[-1]["end"] == local_start:
                    result[-1]["end"] = local_end
                else:
                    result.append(
                        {"frame": index, "start": local_start, "end": local_end}
                    )
            index += 1
    return result


def _plan_track(track, origin=0, shared_rate=None):
    """Plan one track starting at global output sample *origin*."""
    if isinstance(origin, bool) or not isinstance(origin, int) or origin < 0:
        raise PlanError("origin must be a non-negative integer")
    parsed = _parse_track(track)
    pieces = _free_pieces(parsed)
    slices = _slices(parsed, pieces)
    warnings = _track_contradictions(track, parsed, shared_rate)

    mapping = []
    cursor = origin
    for item in slices:
        length = item["end"] - item["start"]
        mapping.append(
            {
                "output_start": cursor,
                "output_end": cursor + length,
                "frame": item["frame"],
                "start": item["start"],
                "end": item["end"],
            }
        )
        cursor += length

    count = cursor - origin
    result = {
        "id": parsed["id"],
        "slices": slices,
        "samples": count,
        "global_start": origin,
        "global_end": cursor,
        "mapping": mapping,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def _error_result(track_id, message, origin):
    return {
        "id": track_id,
        "error": message,
        "slices": [],
        "samples": 0,
        "global_start": origin,
        "global_end": origin,
        "mapping": [],
    }


def plan(track, origin=0):
    """Plan a single track (raises :class:`PlanError` for bad metadata)."""
    return _plan_track(track, origin)


def plan_payload(payload):
    """Plan all tracks on one global sample clock.

    A rejected track is reported in place, contributes no output samples and
    does not stop later, independently valid tracks from being playable.
    """
    if not isinstance(payload, dict):
        raise PlanError("input must be a JSON object")

    rate = payload.get("sample_rate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
        raise PlanError("sample_rate must be a positive number")

    raw_tracks = payload.get("tracks")
    if not isinstance(raw_tracks, (list, tuple)):
        raise PlanError("tracks must be an array")

    result = {
        "sample_rate": rate,
        "tracks": [],
        "mapping": [],
        "samples": 0,
        "errors": [],
        "warnings": [],
    }
    cursor = 0
    seen_ids = {}
    for index, raw_track in enumerate(raw_tracks):
        try:
            planned = _plan_track(raw_track, cursor, rate)
        except (PlanError, KeyError, TypeError, ValueError) as error:
            track_id = raw_track.get("id") if isinstance(raw_track, dict) else None
            planned = _error_result(track_id, str(error), cursor)
        result["tracks"].append(planned)

        # Metadata contradictions are reported, but never used to discard an
        # otherwise playable track: the track keeps its slices and its share
        # of the global clock.
        if "error" in planned:
            result["errors"].append(
                {
                    "track_index": index,
                    "id": planned["id"],
                    "error": planned["error"],
                }
            )
        else:
            cursor += planned["samples"]

        track_id = planned["id"]
        for message in planned.get("warnings", []):
            result["warnings"].append(
                {"track_index": index, "id": track_id, "warning": message}
            )

        if track_id is not None:
            if track_id in seen_ids:
                result["warnings"].append(
                    {
                        "track_index": index,
                        "id": track_id,
                        "warning": "duplicate track id (first used at track %d); "
                        "playable ranges stay distinct, but reverse lookup by id "
                        "is ambiguous" % seen_ids[track_id],
                    }
                )
            else:
                seen_ids[track_id] = index

        for segment in planned["mapping"]:
            item = dict(segment)
            item["track"] = track_id
            item["track_index"] = index
            result["mapping"].append(item)

    result["samples"] = cursor
    return result


def _segments(result):
    if "tracks" in result:
        for track in result.get("tracks", []):
            for segment in track.get("mapping", []):
                yield track.get("id"), segment
    else:
        for segment in result.get("mapping", []):
            yield result.get("id"), segment


def locate(result, output_sample):
    """Map a global output sample to ``track/frame/local`` (or ``None``).

    Uses the top-level mapping, which is sorted by ``output_start``, so a seek
    is a binary search rather than a scan over every frame of every track.
    """
    if isinstance(output_sample, bool) or not isinstance(output_sample, int):
        return None
    if output_sample < 0 or output_sample >= result.get("samples", 0):
        return None

    segments = result.get("mapping")
    if segments is None:
        segments = [segment for _track, segment in _segments(result)]

    lo, hi = 0, len(segments)
    while lo < hi:
        mid = (lo + hi) // 2
        segment = segments[mid]
        if output_sample < segment["output_start"]:
            hi = mid
        elif output_sample >= segment["output_end"]:
            lo = mid + 1
        else:
            local = segment["start"] + (output_sample - segment["output_start"])
            return {
                "track": segment.get("track"),
                "frame": segment["frame"],
                "sample": local,
                "local": local,
                "segment": [segment["start"], segment["end"]],
            }
    return None


def source_to_output(result, track, frame, local):
    """Map ``track/frame/local`` back to a global output sample (or ``None``)."""
    if isinstance(local, bool) or not isinstance(local, int):
        return None
    for track_id, segment in _segments(result):
        if track is not None and track_id != track:
            continue
        if segment["frame"] == frame and segment["start"] <= local < segment["end"]:
            return segment["output_start"] + (local - segment["start"])
    return None


def main():
    source, target = sys.argv[1:3]
    with open(source, encoding="utf-8") as stream:
        payload = json.load(stream)
    result = plan_payload(payload)
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    main()
