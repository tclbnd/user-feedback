"""Metadata blocks of a FLAC file."""
from collections import namedtuple

Block = namedtuple("Block", "type offset length body")
NAMES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE", 4: "VORBIS_COMMENT",
         5: "CUESHEET", 6: "PICTURE"}


class FlacError(ValueError):
    pass


def blocks(data):
    if data[:4] != b"fLaC":
        raise FlacError("not a FLAC file")
    pos = 4
    out = []
    while pos + 4 <= len(data):
        kind = data[pos] & 0x7F
        if kind not in NAMES:
            break
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        out.append(Block(kind, pos + 4, length, data[pos + 4:pos + 4 + length]))
        pos += 4 + length
    return out, pos
