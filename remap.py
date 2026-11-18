#!/usr/bin/env python3
"""Reorder container channels into the playback layout by speaker id.

The container/decoder channel order only carries meaning through the explicit
speaker ids.  Output position ``p`` therefore always receives the source
channel whose id equals ``target_speakers[p]``; labels are never sorted or
otherwise used to guess a position.
"""
import json
import sys


def build_mapping(source_speakers, target_speakers):
    """Return ``(status, mapping)`` for one segment.

    ``mapping[p]`` is the source channel index feeding target position ``p``,
    or -1 when that target speaker is absent.  Repeated source ids make the
    metadata ambiguous, so the whole segment is muted.
    """
    seen = set()
    for speaker in source_speakers:
        if speaker in seen:
            return "conflict", [-1] * len(target_speakers)
        seen.add(speaker)

    source_position = {speaker: index for index, speaker in enumerate(source_speakers)}
    mapping = [source_position.get(speaker, -1) for speaker in target_speakers]

    # No overlap at all with the playback layout: report the unknown layout
    # instead of inventing positions from label ordering.
    if all(source_index == -1 for source_index in mapping):
        return "unknown", [-1] * len(target_speakers)

    return "ok", mapping


def _segment_channel(samples, source_index, length):
    """Take ``length`` samples from one source channel, zero-padding if short."""
    if not 0 <= source_index < len(samples):
        return [0] * length
    values = list(samples[source_index][:length])
    if len(values) < length:
        values.extend([0] * (length - len(values)))
    return values


def remap(data):
    target_speakers = list(data.get("target_speakers", []))
    channel_count = len(target_speakers)
    channels = [[] for _ in range(channel_count)]
    segment_report = []
    cursor = 0

    for segment in data.get("segments", []):
        start = segment["start"]
        end = segment["end"]
        source_speakers = list(segment.get("source_speakers", []))
        samples = segment.get("samples", [])
        length = max(0, end - start)

        status, mapping = build_mapping(source_speakers, target_speakers)
        segment_report.append({"status": status, "map": mapping})

        # Layout switching follows the half-open sample interval.  Silence is
        # written at target positions, and uncovered intervals stay silent so
        # neighbouring segments cannot slide into the gap.
        if start > cursor:
            for channel in channels:
                channel.extend([0] * (start - cursor))
            cursor = start
        skip = max(0, cursor - start)

        for position in range(channel_count):
            values = _segment_channel(samples, mapping[position], length)
            channels[position].extend(values[skip:])

        cursor = max(cursor, end)

    return {"channels": channels, "segment_report": segment_report}


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        raise SystemExit("usage: remap.py input.json output.json")

    with open(argv[1], "r", encoding="utf-8") as handle:
        data = json.load(handle)

    result = remap(data)

    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(result, handle)


if __name__ == "__main__":
    main()

