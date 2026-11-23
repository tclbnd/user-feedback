"""IMF Composition Playlists (SMPTE ST 2067-3): segments, sequences, resources, markers, timecode."""
import xml.etree.ElementTree as ET
from fractions import Fraction

CPL_NAMESPACES = ("http://www.smpte-ra.org/schemas/2067-3/2013",)
CC = "http://www.smpte-ra.org/schemas/2067-2/2013"


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
    """Duration of a resource on the composition timeline, in composition edit units."""
    intrinsic = int(text(res, "IntrinsicDuration", "0"))
    entry = int(text(res, "EntryPoint", "0"))
    source = int(text(res, "SourceDuration", str(intrinsic)))
    return Fraction(source), entry, cpl_rate


def parse(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise CplError("not XML: %s" % exc)
    ns = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if local(root.tag) != "CompositionPlaylist" or ns not in CPL_NAMESPACES:
        raise CplError("not an IMF composition playlist")
    cpl_rate = rate(text(root, "EditRate"))
    out = {"title": text(root, "ContentTitle"), "edit_rate": str(cpl_rate), "timecode_start": None}
    tc = child(root, "CompositionTimecode")
    if tc is not None:
        pass
    seg_list = child(root, "SegmentList")
    if seg_list is None:
        raise CplError("composition without a SegmentList")
    total = Fraction(0)
    sequences, files, markers, mismatches = {}, set(), [], []
    for n, seg in enumerate(children(seg_list, "Segment"), 1):
        lengths = {}
        seq_list = child(seg, "SequenceList")
        for seq in (list(seq_list) if seq_list is not None else []):
            if not (seq.tag.startswith("{%s}" % CC) or local(seq.tag) == "MarkerSequence"):
                continue
            kind = local(seq.tag)
            rl = child(seq, "ResourceList")
            length = Fraction(0)
            for res in (list(rl) if rl is not None else []):
                units, entry, own = resource_units(res, cpl_rate)
                if kind == "MarkerSequence":
                    for mk in children(res, "Marker"):
                        offset = Fraction(int(text(mk, "Offset", "0"))) * cpl_rate / own
                        markers.append({"label": text(mk, "Label"), "offset": str(length + offset)})
                else:
                    files.add(len(files))
                length += units
            if kind != "MarkerSequence":
                lengths[kind] = length
                s = sequences.setdefault(kind, {"resources": 0, "duration": Fraction(0)})
                s["resources"] += len(list(rl)) if rl is not None else 0
                s["duration"] += length
        seg_len = lengths.get("MainImageSequence", max(lengths.values(), default=Fraction(0)))
        total += seg_len
    out.update(segments=len(children(seg_list, "Segment")), duration_units=str(total), duration=str(total / cpl_rate),
               sequences={k: {"resources": v["resources"], "duration_units": str(v["duration"])} for k, v in sorted(sequences.items())},
               track_files=len(files), markers=markers, mismatched_segments=mismatches)
    return out
