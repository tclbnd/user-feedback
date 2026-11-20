"""FLV header and tags.

Implements the container parts of the Adobe Video File Format
Specification v10.1: the FLV header (with its declared DataOffset), the
11-byte tag header including the full 32-bit timestamp (24 low bits plus
the Timestamp Extended byte) and the 4-byte PreviousTagSize fields.
"""
from collections import namedtuple

# Tag types, E.4.1
TAG_AUDIO = 8
TAG_VIDEO = 9
TAG_SCRIPT = 18

# Reserved / filter bits of the first tag-header byte (E.4.1).
TAG_TYPE_MASK = 0x1F

Tag = namedtuple("Tag", "type timestamp body offset")
ParsedTagSize = namedtuple("ParsedTagSize", "tag previous_size")


class FlvError(ValueError):
    pass


def header(data):
    """Parse the FLV header (E.2) and return (info, data_offset).

    The header's DataOffset field gives the byte offset of the
    PreviousTagSize0 field (the first tag header starts four bytes later),
    so headers longer than the canonical nine bytes are handled instead of
    assuming the header is exactly nine bytes long.
    """
    if len(data) < 9 or data[:3] != b"FLV":
        raise FlvError("not an FLV file")
    if data[3] != 0x01:
        raise FlvError("unsupported FLV version %d" % data[3])
    flags = data[4]
    data_offset = int.from_bytes(data[5:9], "big")
    if data_offset < 9 or data_offset > len(data):
        raise FlvError("invalid FLV DataOffset %d" % data_offset)
    info = {"has_audio": bool(flags & 4), "has_video": bool(flags & 1)}
    return info, data_offset


def iter_tags(data):
    """Yield (Tag, previous_tag_size) for every complete tag in *data*.

    The header's DataOffset points at the four zero bytes of
    PreviousTagSize0; the first 11-byte tag header follows them.  Each tag
    is followed by a 4-byte PreviousTagSize giving the size of the
    previous tag.  A tag is yielded only when its 11-byte header, full
    body and trailing PreviousTagSize are all present, so an incomplete
    tag at the end of a recording is skipped rather than truncated.
    """
    info, data_offset = header(data)
    # DataOffset marks the start of the PreviousTagSize0 field; the first
    # tag header follows those four (zero) bytes.
    pos = data_offset + 4
    while pos + 11 <= len(data):
        tag_start = pos
        # PreviousTagSize immediately precedes the tag header.
        previous_size = int.from_bytes(data[tag_start - 4:tag_start], "big")
        kind = data[tag_start] & TAG_TYPE_MASK
        body_size = int.from_bytes(data[tag_start + 1:tag_start + 4], "big")
        ts_low = int.from_bytes(data[tag_start + 4:tag_start + 7], "big")
        ts_ext = data[tag_start + 7]
        # Full timestamp: Timestamp Extended carries the most significant
        # byte, giving an unsigned 32-bit millisecond timestamp.
        timestamp = (ts_ext << 24) | ts_low
        body_start = tag_start + 11
        body_end = body_start + body_size
        # Require the complete body and the trailing PreviousTagSize.
        if body_end + 4 > len(data):
            break
        body = data[body_start:body_end]
        yield ParsedTagSize(Tag(kind, timestamp, body, tag_start), previous_size)
        pos = body_end + 4
    return info


def tags(data):
    """Backwards-compatible wrapper returning (header_info, tag list).

    Only complete tags (body plus trailing PreviousTagSize fully present)
    are returned, so a truncated tag at the file tail is skipped.
    """
    info, _ = header(data)
    return info, [parsed.tag for parsed in iter_tags(data)]

