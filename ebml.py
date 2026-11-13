"""EBML primitives for Matroska container repair.

Element IDs are handled as ints *including* their length-marker bits
(e.g. Segment = 0x18538067).  Element sizes are VINTs where the marker
bit is stripped; an all-ones value means "unknown size".
"""


class EBMLError(Exception):
    """Parse error that carries the byte offset where it was detected."""

    def __init__(self, message, offset):
        super().__init__("%s (at byte offset %d)" % (message, offset))
        self.message = message
        self.offset = offset


# ---- element IDs -----------------------------------------------------------
ID_EBML = 0x1A45DFA3
ID_SEGMENT = 0x18538067
ID_SEEK_HEAD = 0x114D9B74
ID_SEEK = 0x4DBB
ID_SEEK_ID = 0x53AB
ID_SEEK_POSITION = 0x53AC
ID_INFO = 0x1549A966
ID_TIMECODE_SCALE = 0x2AD7B1
ID_DURATION = 0x4489
ID_MUXING_APP = 0x4D80
ID_WRITING_APP = 0x5741
ID_TRACKS = 0x1654AE6B
ID_TRACK_ENTRY = 0xAE
ID_TRACK_NUMBER = 0xD7
ID_TRACK_UID = 0x73C5
ID_TRACK_TYPE = 0x83
ID_FLAG_LACING = 0x9C
ID_CODEC_ID = 0x86
ID_CODEC_PRIVATE = 0x63A2
ID_VIDEO = 0xE0
ID_AUDIO = 0xE1
ID_CLUSTER = 0x1F43B675
ID_TIMECODE = 0xE7
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
ID_CRC32 = 0xBF
ID_TAGS = 0x1254C367
ID_ATTACHMENTS = 0x1941A469
ID_CHAPTERS = 0x1043A770

# IDs that are legal direct children of Segment; used to find the end of
# an unknown-size Cluster.
SEGMENT_LEVEL_IDS = frozenset((
    ID_SEEK_HEAD, ID_INFO, ID_TRACKS, ID_CLUSTER, ID_CUES,
    ID_TAGS, ID_ATTACHMENTS, ID_CHAPTERS,
))

TRACK_TYPE_VIDEO = 0x01
TRACK_TYPE_AUDIO = 0x02

LACING_NONE = 0
LACING_XIPH = 1
LACING_FIXED = 2
LACING_EBML = 3


# ---- low level readers -----------------------------------------------------
def read_id(data, pos, end=None):
    """Read an element ID. Returns (id_int, next_pos)."""
    if end is None:
        end = len(data)
    if pos >= end:
        raise EBMLError("truncated element ID", pos)
    b0 = data[pos]
    if b0 & 0x80:
        width = 1
    elif b0 & 0x40:
        width = 2
    elif b0 & 0x20:
        width = 3
    elif b0 & 0x10:
        width = 4
    else:
        raise EBMLError("invalid element ID 0x%02x" % b0, pos)
    if pos + width > end:
        raise EBMLError("truncated %d-byte element ID" % width, pos)
    return int.from_bytes(data[pos:pos + width], "big"), pos + width


def read_size(data, pos, end=None):
    """Read an element size VINT. Returns (value_or_None_unknown, next_pos)."""
    if end is None:
        end = len(data)
    if pos >= end:
        raise EBMLError("truncated element size", pos)
    b0 = data[pos]
    width = 0
    for n in range(1, 9):
        if b0 & (1 << (8 - n)):
            width = n
            break
    if width == 0:
        raise EBMLError("invalid element size 0x%02x" % b0, pos)
    if pos + width > end:
        raise EBMLError("truncated %d-byte element size" % width, pos)
    value = int.from_bytes(data[pos:pos + width], "big") & ((1 << (7 * width)) - 1)
    if value == (1 << (7 * width)) - 1:
        return None, pos + width  # unknown size
    return value, pos + width


def read_vint_value(data, pos, end=None):
    """Read a VINT as a plain value (track numbers, lace sizes)."""
    value, next_pos = read_size(data, pos, end)
    return value, next_pos


def read_signed_vint_value(data, pos, end=None):
    """Read a VINT as a signed value (EBML lacing size deltas)."""
    if end is None:
        end = len(data)
    if pos >= end:
        raise EBMLError("truncated signed VINT", pos)
    b0 = data[pos]
    width = 0
    for n in range(1, 9):
        if b0 & (1 << (8 - n)):
            width = n
            break
    if width == 0 or pos + width > end:
        raise EBMLError("truncated signed VINT", pos)
    raw = int.from_bytes(data[pos:pos + width], "big") & ((1 << (7 * width)) - 1)
    bias = (1 << (7 * width - 1)) - 1
    return raw - bias, pos + width


# ---- writers ---------------------------------------------------------------
def encode_id(elem_id):
    width = max(1, (elem_id.bit_length() + 7) // 8)
    return elem_id.to_bytes(width, "big")


def encode_size(value, width=None):
    """Encode a known size; picks the minimal width unless given."""
    if width is None:
        width = 1
        while value >= (1 << (7 * width)) - 1:  # keep all-ones reserved
            width += 1
    if value >= (1 << (7 * width)):
        raise ValueError("size %d does not fit in %d bytes" % (value, width))
    marker = 1 << (7 * width)
    return (marker | value).to_bytes(width, "big")


def encode_unknown_size(width=1):
    return ((1 << (7 * width)) - 1 | (1 << (7 * width))).to_bytes(width, "big")


def encode_vint(value):
    """Minimal-width VINT for track numbers / lace sizes."""
    return encode_size(value)


def encode_uint(value):
    if value < 0:
        raise ValueError("unsigned element cannot be negative")
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def element(elem_id, payload):
    return encode_id(elem_id) + encode_size(len(payload)) + payload


def decode_uint(payload):
    return int.from_bytes(payload, "big")


def decode_int(payload):
    return int.from_bytes(payload, "big", signed=True)
