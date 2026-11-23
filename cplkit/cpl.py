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


def resource_units(res, cpl_rate):
    """Duration of a resource on the composition timeline, in composition edit units.

    A resource plays SourceDuration edit units from EntryPoint, RepeatCount
    times. Missing values take their defaults: EntryPoint 0, SourceDuration
    the rest of the track file after the entry point, RepeatCount 1, EditRate
    the composition's. Durations are counted in the resource's own edit rate
    and converted to the composition's.
    """
    own_text = text(res, "EditRate")
    own = rate(own_text) if own_text else cpl_rate
    intrinsic = int(text(res, "IntrinsicDuration", "0"))
    entry = int(text(res, "EntryPoint", "0"))
    source = int(text(res, "SourceDuration", str(intrinsic - entry)))
    repeat = int(text(res, "RepeatCount", "1"))
    return Fraction(source * repeat) * cpl_rate / own, own


def parse(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise CplError("not XML: %s" % exc)
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if local(root.tag) != "CompositionPlaylist" or ns not in CPL_NAMESPACES:
        raise CplError("not an IMF composition playlist")
    cpl_rate = rate(text(root, "EditRate"))
    out = {"title": text(root, "ContentTitle"), "edit_rate": str(cpl_rate),
           "timecode_start": None, "content_kind": text(root, "ContentKind")}
    tc = child(root, "CompositionTimecode")
    if tc is not None:
        out["timecode_start"] = text(tc, "TimecodeStartAddress")
    seg_list = child(root, "SegmentList")
    if seg_list is None:
        raise CplError("composition without a SegmentList")
    total = Fraction(0)
    sequences, files, markers, mismatches = {}, set(), [], []
    tracks = {}
    for n, seg in enumerate(children(seg_list, "Segment"), 1):
        seg_lengths = []
        image_len = None
        seq_list = child(seg, "SequenceList")
        for seq in (list(seq_list) if seq_list is not None else []):
            kind = local(seq.tag)
            rl = child(seq, "ResourceList")
            resources = list(rl) if rl is not None else []
            length = Fraction(0)
            for res in resources:
                units, own = resource_units(res, cpl_rate)
                if kind == "MarkerSequence":
                    for mk in children(res, "Marker"):
                        offset = Fraction(int(text(mk, "Offset", "0"))) * cpl_rate / own
                        markers.append({"label": text(mk, "Label"),
                                        "offset": str(total + length + offset)})
                track_file = text(res, "TrackFileId")
                if track_file:
                    files.add(track_file)
                length += units
            seg_lengths.append(length)
            if kind == "MainImageSequence" and image_len is None:
                image_len = length
            s = sequences.setdefault(kind, {"resources": 0, "duration": Fraction(0)})
            s["resources"] += len(resources)
            s["duration"] += length
            track_id = text(seq, "TrackId")
            if track_id:
                tracks.setdefault(kind, set()).add(track_id)
        if seg_lengths and any(length != seg_lengths[0] for length in seg_lengths):
            mismatches.append(n)
        total += image_len if image_len is not None else max(seg_lengths, default=Fraction(0))
    out.update(segments=len(children(seg_list, "Segment")), duration_units=str(total), duration=str(total / cpl_rate),
               sequences={k: {"resources": v["resources"], "duration_units": str(v["duration"])} for k, v in sorted(sequences.items())},
               track_files=len(files), markers=markers,
               tracks={k: len(v) for k, v in sorted(tracks.items())},
               mismatched_segments=mismatches)
    return out
