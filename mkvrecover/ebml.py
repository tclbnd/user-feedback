"""Minimal, dependency-free EBML / Matroska primitives.

All offsets exposed by this module are absolute byte offsets in the source
file unless a name explicitly says "relative".
"""

# ---- EBML / Matroska element IDs -------------------------------------------------
ID_EBML_HEADER = 0x1A45DFA3
ID_SEGMENT = 0x18538067
ID_SEEK_HEAD = 0x114D9B74
ID_SEEK = 0x4DBB
ID_SEEK_ID = 0x53AB
ID_SEEK_POSITION = 0x53AC
ID_INFO = 0x1549A966
ID_TIMESTAMP_SCALE = 0x2AD7B1
ID_DURATION = 0x4489
ID_MUXING_APP = 0x4D80
ID_WRITING_APP = 0x5741
ID_TITLE = 0x7BA9
ID_DATE_UTC = 0x4461
ID_SEGMENT_UID = 0x73A4
ID_TRACKS = 0x1654AE6B
ID_TRACK_ENTRY = 0xAE
ID_TRACK_NUMBER = 0xD7
ID_TRACK_UID = 0x73C5
ID_TRACK_TYPE = 0x83
ID_CODEC_ID = 0x86
ID_CODEC_PRIVATE = 0x63A2
ID_FLAG_LACING = 0x9C
ID_FLAG_DEFAULT = 0x88
ID_DEFAULT_DURATION = 0x23E383
ID_VIDEO = 0xE0
ID_AUDIO = 0xE1
ID_PIXEL_WIDTH = 0xB0
ID_PIXEL_HEIGHT = 0xBA
ID_SAMPLING_FREQ = 0xB5
ID_CHANNELS = 0x9F
ID_CLUSTER = 0x1F43B675
ID_TIMESTAMP = 0xE7
ID_SIMPLE_BLOCK = 0xA3
ID_BLOCK_GROUP = 0xA0
ID_BLOCK = 0xA1
ID_REFERENCE_BLOCK = 0xFB
ID_CUES = 0x1C53BB6B
ID_CUE_POINT = 0xBB
ID_CUE_TIME = 0xB3
ID_CUE_TRACK_POSITIONS = 0xB7
ID_CUE_TRACK = 0xF7
ID_CUE_CLUSTER_POSITION = 0xF1
ID_CUE_RELATIVE_POSITION = 0xF0
ID_VOID = 0xEC
ID_CRC_32 = 0xBF

CONTAINER_IDS = frozenset((
    ID_EBML_HEADER, ID_SEGMENT, ID_SEEK_HEAD, ID_SEEK, ID_INFO, ID_TRACKS,
    ID_TRACK_ENTRY, ID_VIDEO, ID_AUDIO, ID_CLUSTER, ID_BLOCK_GROUP, ID_CUES,
    ID_CUE_POINT, ID_CUE_TRACK_POSITIONS,
))

TOP_LEVEL_SEGMENT_IDS = frozenset((
    ID_SEEK_HEAD, ID_INFO, ID_TRACKS, ID_CLUSTER, ID_CUES, ID_VOID, ID_CRC_32,
))

BLOCK_ELEMENT_IDS = frozenset((ID_SIMPLE_BLOCK, ID_BLOCK))


class EBMLSyntaxError(Exception):
    """Raised when bytes cannot be interpreted as an EBML element."""

    def __init__(self, message, offset):
        super().__init__("at byte %d: %s" % (offset, message))
        self.offset = offset


class Truncated(EBMLSyntaxError):
    """The element header or payload runs past the available bytes."""


def read_vint(buf, offset, allow_unknown=True):
    """Return (length_in_bytes, value). value is None for an unknown marker."""
    if offset >= len(buf):
        raise Truncated("no byte for VINT", offset)
    first = buf[offset]
    width = 0
    for i in range(8):
        if first & (0x80 >> i):
            width = i + 1
            break
    if width == 0:
        raise EBMLSyntaxError("VINT length marker all zero", offset)
    if offset + width > len(buf):
        raise Truncated("VINT runs past end of data", offset)
    value = first & ((1 << (8 - width)) - 1)
    for k in range(1, width):
        value = (value << 8) | buf[offset + k]
    if allow_unknown and value == (1 << (7 * width)) - 1:
        return width, None
    return width, value


def read_element_id(buf, offset):
    """Return (length, raw_id). Element IDs keep their marker bits."""
    if offset >= len(buf):
        raise Truncated("no byte for element id", offset)
    first = buf[offset]
    width = 0
    for i in range(8):
        if first & (0x80 >> i):
            width = i + 1
            break
    if width == 0:
        raise EBMLSyntaxError("element id length marker all zero", offset)
    if offset + width > len(buf):
        raise Truncated("element id runs past end of data", offset)
    raw = int.from_bytes(buf[offset:offset + width], "big")
    return width, raw


def read_uint(buf, offset, length):
    if offset + length > len(buf):
        raise Truncated("uint runs past end of data", offset)
    value = 0
    for b in buf[offset:offset + length]:
        value = (value << 8) | b
    return value


def vint_min_width(value):
    """Smallest VINT width that can encode value as a known size."""
    for width in range(1, 9):
        if value < (1 << (7 * width)) - 1:
            return width
    raise ValueError("value does not fit in 8-byte VINT")


def encode_vint(value, width=None, unknown=False):
    if unknown:
        width = width or 8
        return bytes([0x01 << (8 - width)]) + b"\xff" * (width - 1)
    if value < 0:
        raise ValueError("VINT cannot encode negative values")
    width = width or vint_min_width(value)
    if value >= (1 << (7 * width)) - 1:
        raise ValueError("value collides with the unknown-size marker")
    marker = 0x80 >> (width - 1)
    out = bytearray(width)
    rest = value
    for i in range(width - 1, -1, -1):
        out[i] = rest & 0xFF
        rest >>= 8
    out[0] |= marker
    return bytes(out)


def encode_element_id(eid):
    width = (eid.bit_length() + 7) // 8
    if width == 0 or width > 4:
        raise EBMLSyntaxError("element id width out of range", 0)
    raw = eid.to_bytes(width, "big")
    # A legal element-id VINT has its length marker in the leading byte.
    if not (raw[0] & (0x80 >> (width - 1))):
        raise EBMLSyntaxError("element id lacks length marker", 0)
    return raw


def encode_uint(value):
    if value == 0:
        return b"\x00"
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def encode_float(value):
    import struct
    return struct.pack(">d", float(value))


class Element:
    """One parsed EBML element with source-relative bookkeeping."""

    __slots__ = (
        "eid", "header_offset", "id_len", "size_len", "size",
        "payload_offset", "payload_end", "unknown_size", "children",
        "truncated", "truncation",
    )

    def __init__(self, eid, header_offset, id_len, size_len, size,
                 payload_offset, payload_end, unknown_size=False):
        self.eid = eid
        self.header_offset = header_offset
        self.id_len = id_len
        self.size_len = size_len
        self.size = size                      # None for unknown size
        self.payload_offset = payload_offset
        self.payload_end = payload_end        # end of actually available data
        self.unknown_size = unknown_size
        self.children = []
        self.truncated = False
        self.truncation = None                # 'header' | 'payload' | None

    @property
    def header_len(self):
        return self.id_len + self.size_len

    @property
    def end_offset(self):
        """One past the element as far as bytes are available."""
        return self.payload_end

    def payload(self, buf):
        return buf[self.payload_offset:self.payload_end]


def scan_children(buf, start, end, container_ids=None, depth=0, max_depth=6):
    """Parse EBML elements in buf[start:end].

    Returns (elements, tail_offset).  Truncation is recorded on the element
    rather than raised so callers can salvage complete siblings.
    """
    if container_ids is None:
        container_ids = CONTAINER_IDS
    elements = []
    offset = start
    while offset < end:
        elem_start = offset
        try:
            id_len, eid = read_element_id(buf, offset)
        except Truncated as exc:
            raise Truncated("element id incomplete", elem_start) from exc
        size_off = offset + id_len
        try:
            size_len, size = read_vint(buf, size_off, allow_unknown=True)
        except Truncated as exc:
            phantom = Element(eid, elem_start, id_len, 0, None,
                              size_off, end)
            phantom.truncated = True
            phantom.truncation = "header"
            elements.append(phantom)
            return elements, end
        payload_off = size_off + size_len
        unknown = size is None
        if unknown:
            declared_end = end
        else:
            declared_end = payload_off + size
        available_end = min(declared_end, end)
        elem = Element(eid, elem_start, id_len, size_len, size,
                       payload_off, available_end, unknown_size=unknown)
        if not unknown and declared_end > end:
            elem.truncated = True
            elem.truncation = "payload"
            elements.append(elem)
            return elements, end
        if (depth < max_depth and eid in container_ids
                and available_end > payload_off):
            elem.children, _tail = scan_children(
                buf, payload_off, available_end, container_ids,
                depth + 1, max_depth)
        elements.append(elem)
        offset = available_end if not unknown else available_end
        if unknown:
            # Unknown-size element consumes the rest of this scope; callers
            # needing sibling recovery handle it at a higher level.
            return elements, offset
    return elements, offset


def find_child(elem, eid):
    for child in getattr(elem, "children", ()):
        if child.eid == eid:
            return child
    return None


def find_children(elem, eid):
    return [c for c in getattr(elem, "children", ()) if c.eid == eid]


def child_uint(buf, elem, eid, default=None):
    child = find_child(elem, eid)
    if child is None:
        return default
    return read_uint(buf, child.payload_offset,
                     child.payload_end - child.payload_offset)


def child_bytes(buf, elem, eid):
    child = find_child(elem, eid)
    if child is None:
        return None
    return buf[child.payload_offset:child.payload_end]


def child_text(buf, elem, eid):
    raw = child_bytes(buf, elem, eid)
    if raw is None:
        return None
    return raw.decode("utf-8", errors="replace")


# ---- Block / lacing --------------------------------------------------------------

LACING_NONE = 0
LACING_XIPH = 1
LACING_EBML = 3
LACING_FIXED = 2


def parse_block(buf, payload_offset, payload_end):
    """Parse a SimpleBlock/Block payload.

    Returns dict with track, signed relative time, flags, lacing, frames
    (list of (offset,length) into buf), and header length.  Raises
    EBMLSyntaxError for structurally impossible blocks.
    """
    tn_len, track = read_vint(buf, payload_offset, allow_unknown=False)
    t_off = payload_offset + tn_len
    if t_off + 2 > payload_end:
        raise Truncated("block missing relative timestamp", payload_offset)
    rel_time = int.from_bytes(buf[t_off:t_off + 2], "big", signed=True)
    flags_off = t_off + 2
    if flags_off >= payload_end:
        raise Truncated("block missing flags byte", payload_offset)
    flags = buf[flags_off]
    lacing = (flags >> 1) & 0x03
    frame_off = flags_off + 1
    data_end = payload_end
    if lacing == LACING_NONE:
        frames = [(frame_off, data_end - frame_off)] if data_end > frame_off else []
        if not frames:
            raise Truncated("block has no frame data", payload_offset)
    else:
        if frame_off >= payload_end:
            raise Truncated("laced block missing frame count", payload_offset)
        count = buf[frame_off] + 1
        frame_off += 1
        sizes = _laced_frame_sizes(buf, frame_off, data_end, count, lacing,
                                   payload_offset)
        frames = sizes
    return {
        "track": track,
        "relative_time": rel_time,
        "flags": flags,
        "keyframe": bool(flags & 0x80),
        "lacing": lacing,
        "frames": frames,
        "header_len": frame_off - payload_offset,
    }


def _laced_frame_sizes(buf, offset, data_end, count, lacing, block_off):
    frames = []
    if lacing == LACING_FIXED:
        total = data_end - offset
        if count == 0 or total % count:
            raise EBMLSyntaxError(
                "fixed lacing payload not divisible by frame count", block_off)
        piece = total // count
        for i in range(count):
            frames.append((offset + i * piece, piece))
        return frames
    sizes = []
    cursor = offset
    if lacing == LACING_XIPH:
        for i in range(count - 1):
            total_size = 0
            while True:
                if cursor >= data_end:
                    raise Truncated("Xiph lacing size truncated", block_off)
                byte = buf[cursor]
                cursor += 1
                total_size += byte
                if byte != 255:
                    break
            sizes.append(total_size)
    elif lacing == LACING_EBML:
        for i in range(count - 1):
            width, delta = _read_signed_vint(buf, cursor)
            cursor += width
            if i == 0:
                sizes.append(delta)          # first value is absolute
            else:
                sizes.append(sizes[-1] + delta)
        if any(s < 0 for s in sizes):
            raise EBMLSyntaxError("EBML lacing produced negative size",
                                  block_off)
    else:  # pragma: no cover - guarded by caller
        raise EBMLSyntaxError("unknown lacing type %d" % lacing, block_off)
    used = sum(sizes)
    last = (data_end - cursor) - used
    if last < 0:
        raise Truncated("laced frames exceed block payload", block_off)
    sizes.append(last)
    pos = cursor
    for size in sizes:
        if pos + size > data_end:
            raise Truncated("laced frame runs past block", block_off)
        frames.append((pos, size))
        pos += size
    return frames


def _read_signed_vint(buf, offset):
    width, raw = read_vint(buf, offset, allow_unknown=True)
    if raw is None:
        raise EBMLSyntaxError("unknown marker inside lacing sizes", offset)
    bias = 1 << (7 * width - 1)
    return width, raw - bias


def de_lace(buf, block):
    """Return the concatenated frame payloads as separate byte strings."""
    return [buf[off:off + length] for off, length in block["frames"]]
