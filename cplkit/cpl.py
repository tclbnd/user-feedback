"""IMF Composition Playlists (SMPTE ST 2067-3): segments, sequences, resources, markers, timecode."""
import xml.etree.ElementTree as ET
from fractions import Fraction

CPL_NAMESPACES = (
    "http://www.smpte-ra.org/schemas/2067-3/2013",
    "http://www.smpte-ra.org/schemas/2067-3/2016",
)


class CplError(ValueError):
    pass


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(el, name):
    for c in el:
        if local(c.tag) == name:
            return c
    return None


def children(el, name):
    return [c for c in el if local(c.tag) == name]


def text(el, name, default=None):
    c = child(el, name)
    return c.text.strip() if c is not None and c.text and c.text.strip() else default


def rate(value):
    num, den = value.split()
    return Fraction(int(num), int(den))


def resource_duration(res, cpl_rate):
    """Duration of a resource on the composition timeline, in composition edit units.

    A resource plays SourceDuration edit units from EntryPoint, RepeatCount
    times, counted in the resource's own EditRate and converted to the
    composition EditRate.  Defaults per SMPTE ST 2067-3: EntryPoint 0,
    SourceDuration = IntrinsicDuration - EntryPoint, RepeatCount 1,
    EditRate = the composition's.
    """
    own = rate(text(res, "EditRate")) if text(res, "EditRate") is not None else cpl_rate
    intrinsic = int(text(res, "IntrinsicDuration", "0"))
    entry = int(text(res, "EntryPoint", "0"))
    source_value = text(res, "SourceDuration")
    source = int(source_value) if source_value is not None else intrinsic - entry
    repeat = int(text(res, "RepeatCount", "1"))
    return Fraction(source * repeat) * cpl_rate / own, own


def parse_timecode(value):
    h, m, s, f = value.replace(";", ":").split(":")
    return int(h), int(m), int(s), int(f)


def timecode_to_frames(h, m, s, f, tc_rate, drop):
    """Frame count of a timecode address at the timecode rate."""
    total_minutes = 60 * h + m
    if drop:
        # Frames dropped per minute at 30 fps: 2; at 60 fps: 4.
        # Minutes divisible by 10 (minute 0, 10, 20, ...) skip nothing.
        dropped = tc_rate // 15
        skip_minutes = total_minutes - (total_minutes + 9) // 10
        return (tc_rate * 60 * total_minutes + tc_rate * s + f
                - dropped * skip_minutes)
    return ((h * 60 + m) * 60 + s) * tc_rate + f


def frames_to_timecode(frame_count, tc_rate, drop):
    """Format a frame count as HH:MM:SS:FF, applying the drop-frame rule."""
    if drop:
        dropped = tc_rate // 15
        frames_per_minute = tc_rate * 60 - dropped
        # Standard drop-frame inverse: nominal minute of the frame count,
        # then add the dropped frames skipped up to that minute.
        minute = (frame_count - frame_count // (frames_per_minute * 10)) // frames_per_minute
        f = frame_count + dropped * (minute - minute // 10)
    else:
        f = frame_count
    h, x = divmod(f, tc_rate * 3600)
    m, x = divmod(x, tc_rate * 60)
    s, fr = divmod(x, tc_rate)
    sep = ";" if drop else ":"
    return "%02d:%02d:%02d" % (h, m, s) + sep + "%02d" % fr


def parse(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise CplError("not XML: %s" % exc)
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if local(root.tag) != "CompositionPlaylist" or ns not in CPL_NAMESPACES:
        raise CplError("not an IMF composition playlist")
    cpl_rate = rate(text(root, "EditRate"))

    tc = child(root, "CompositionTimecode")
    timecode_start = None
    tc_rate, drop, start_frames = 0, False, 0
    if tc is not None:
        timecode_start = text(tc, "TimecodeStartAddress")
        tc_rate = int(text(tc, "TimecodeRate", "0"))
        drop = text(tc, "TimecodeDropFrame", "false").lower() == "true"
        if timecode_start is not None:
            start_frames = timecode_to_frames(*parse_timecode(timecode_start), tc_rate, drop)

    seg_list = child(root, "SegmentList")
    if seg_list is None:
        raise CplError("composition without a SegmentList")
    total = Fraction(0)
    sequences, tracks, files, markers, mismatches = {}, {}, set(), [], []
    for n, seg in enumerate(children(seg_list, "Segment"), 1):
        lengths = {}
        segment_start = total
        seq_list = child(seg, "SequenceList")
        for seq in (list(seq_list) if seq_list is not None else []):
            kind = local(seq.tag)
            if not kind.endswith("Sequence"):
                continue
            track_id = text(seq, "TrackId")
            if track_id is not None:
                tracks.setdefault(kind, set()).add(track_id)
            rl = child(seq, "ResourceList")
            res_elements = children(rl, "Resource") if rl is not None else []
            length = Fraction(0)
            for res in res_elements:
                units, own_rate = resource_duration(res, cpl_rate)
                if kind == "MarkerSequence":
                    for mk in children(res, "Marker"):
                        offset = Fraction(int(text(mk, "Offset", "0"))) * cpl_rate / own_rate
                        position = segment_start + length + offset
                        entry = {"label": text(mk, "Label"), "offset": str(position)}
                        if tc is not None:
                            elapsed = position * tc_rate / cpl_rate
                            whole = elapsed.numerator // elapsed.denominator
                            entry["timecode"] = frames_to_timecode(start_frames + whole, tc_rate, drop)
                        else:
                            entry["timecode"] = None
                        markers.append(entry)
                else:
                    track_file = text(res, "TrackFileId")
                    if track_file is not None:
                        files.add(track_file)
                length += units
            lengths[kind] = length
            if kind != "MarkerSequence":
                s = sequences.setdefault(kind, {"resources": 0, "duration": Fraction(0)})
                s["resources"] += len(res_elements)
                s["duration"] += length
        if len(set(lengths.values())) > 1:
            mismatches.append(n)
        seg_len = lengths.get("MainImageSequence", max(lengths.values(), default=Fraction(0)))
        total += seg_len
    return {
        "title": text(root, "ContentTitle"),
        "edit_rate": str(cpl_rate),
        "timecode_start": timecode_start,
        "segments": len(children(seg_list, "Segment")),
        "duration_units": str(total),
        "duration": str(total / cpl_rate),
        "sequences": {k: {"resources": v["resources"], "duration_units": str(v["duration"])}
                      for k, v in sorted(sequences.items())},
        "track_files": len(files),
        "markers": markers,
        "content_kind": text(root, "ContentKind"),
        "tracks": {k: len(v) for k, v in sorted(tracks.items())},
        "mismatched_segments": mismatches,
    }
