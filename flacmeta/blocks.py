"""Metadata block walking as defined by RFC 9639 (the FLAC format).

A FLAC file is the ``fLaC`` marker followed by a chain of metadata block
headers (one byte of flags/type plus a 24-bit big-endian body length) and
their bodies.  The high bit of the first header byte marks the last metadata
block; everything after it is audio, never metadata.
"""
from collections import namedtuple

Block = namedtuple("Block", "type offset length body")

STREAMINFO = 0
PADDING = 1
APPLICATION = 2
SEEKTABLE = 3
VORBIS_COMMENT = 4
CUESHEET = 5
PICTURE = 6

NAMES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE",
         4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}


class FlacError(ValueError):
    pass


def _syncsafe(b):
    """Decode an ID3v2 synchsafe integer (four bytes, seven bits each)."""
    value = 0
    for byte in b:
        if byte & 0x80:
            raise FlacError("invalid ID3v2 synchsafe size")
        value = (value << 7) | byte
    return value


def _skip_id3v2(data):
    """Return the offset of the FLAC marker, skipping a leading ID3v2 tag.

    RFC 9639 permits an ID3v2 tag before the ``fLaC`` marker.  Anything else
    before the marker makes the file not a FLAC.
    """
    pos = 0
    while data[pos:pos + 3] == b"ID3":
        if len(data) < pos + 10:
            raise FlacError("truncated ID3v2 header")
        major = data[pos + 3]
        if major == 0xFF:
            raise FlacError("invalid ID3v2 version")
        flags = data[pos + 5]
        size = _syncsafe(data[pos + 6:pos + 10])
        # Footer flag: a 10-byte footer follows the frames.
        end = pos + 10 + size + (10 if flags & 0x10 else 0)
        if end > len(data):
            raise FlacError("truncated ID3v2 tag")
        pos = end
    if data[pos:pos + 4] != b"fLaC":
        raise FlacError("not a FLAC file")
    return pos


def blocks(data):
    """Walk the metadata blocks.

    Returns ``(blocks, audio_offset)`` where each block is a ``Block`` of
    ``(type, body_offset, body_length, body)`` and ``audio_offset`` points at
    the first byte after the last metadata block header.  Raises
    :class:`FlacError` on any malformed or truncated metadata so callers never
    read audio frames as if they were metadata.
    """
    if not data:
        raise FlacError("empty file")
    start = _skip_id3v2(data)
    pos = start + 4
    out = []
    while True:
        if pos + 4 > len(data):
            raise FlacError("truncated metadata block header")
        header = data[pos]
        last = bool(header & 0x80)
        kind = header & 0x7F
        if kind not in NAMES:
            raise FlacError("unknown metadata block type %d" % kind)
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        body_start = pos + 4
        body_end = body_start + length
        if body_end > len(data):
            raise FlacError("truncated %s block body" % NAMES[kind])
        out.append(Block(kind, body_start, length, data[body_start:body_end]))
        pos = body_end
        if last:
            break
    return out, pos
