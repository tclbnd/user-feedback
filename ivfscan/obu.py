"""AV1 OBUs inside a temporal unit."""
from collections import namedtuple

Obu = namedtuple("Obu", "type temporal_id spatial_id payload")
NAMES = {1: "sequence_header", 2: "temporal_delimiter", 3: "frame_header", 4: "tile_group", 5: "metadata",
         6: "frame", 7: "redundant_frame_header", 8: "tile_list", 15: "padding"}


def leb128(b, p):
    """AV1 leb128(): up to 8 bytes, 7 bits each, high bit = continuation."""
    value, shift = 0, 0
    for _ in range(8):
        if p >= len(b):
            raise IndexError("truncated leb128")
        byte = b[p]
        p += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, p
        shift += 7
    raise ValueError("invalid leb128 (more than 8 bytes)")


def obus(tu):
    """Split a temporal unit into OBUs (AV1 spec 5.3)."""
    out, pos = [], 0
    while pos < len(tu):
        h = tu[pos]
        pos += 1
        if h & 0x80:
            raise ValueError("obu_forbidden_bit is not zero")
        kind = (h >> 3) & 0xF
        extension = (h >> 2) & 0x1
        has_size = (h >> 1) & 0x1
        reserved = h & 0x1
        if reserved:
            raise ValueError("obu_reserved_1bit is not zero")
        temporal_id = spatial_id = 0
        if extension:
            # obu_extension_header(): temporal_id 3, spatial_id 2, reserved 3.
            if pos >= len(tu):
                raise IndexError("truncated OBU extension header")
            ext = tu[pos]
            pos += 1
            temporal_id = (ext >> 5) & 0x7
            spatial_id = (ext >> 3) & 0x3
            if ext & 0x7:
                raise ValueError("obu_extension_header reserved bits are not zero")
        if has_size:
            size, pos = leb128(tu, pos)
            end = pos + size
            if end > len(tu):
                raise IndexError("OBU payload runs past end of temporal unit")
        else:
            # obu_has_size_field == 0: the OBU extends to the end of the
            # temporal unit (only the last OBU may do this).
            end = len(tu)
        out.append(Obu(kind, temporal_id, spatial_id, tu[pos:end]))
        pos = end
    return out
