"""AV1 OBUs inside a temporal unit."""
from collections import namedtuple

Obu = namedtuple("Obu", "type temporal_id spatial_id payload")
NAMES = {1: "sequence_header", 2: "temporal_delimiter", 3: "frame_header", 4: "tile_group", 5: "metadata",
         6: "frame", 7: "redundant_frame_header", 8: "tile_list", 15: "padding"}


def leb128(b, p):
    return b[p] & 0x7F, p + 1


def obus(tu):
    out, pos = [], 0
    while pos < len(tu):
        h = tu[pos]
        kind = (h >> 3) & 0xF
        pos += 1
        size, pos = leb128(tu, pos)
        out.append(Obu(kind, 0, 0, tu[pos:pos + size]))
        pos += size
    return out
