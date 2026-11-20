"""Metadata block walking for a FLAC file, per RFC 9639.

A FLAC file starts with the four-byte ``fLaC`` marker followed by a chain of
metadata block headers.  Each header is one byte of flags (high bit =
last-metadata-block marker, low seven bits = block type) plus a 24-bit big
endian block length.  Some deliveries carry a leading ID3v2 tag, which is not
part of RFC 9639 but is tolerated by skipping it.
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

NAMES = {
    STREAMINFO: "STREAMINFO",
    PADDING: "PADDING",
    APPLICATION: "APPLICATION",
    SEEKTABLE: "SEEKTABLE",
    VORBIS_COMMENT: "VORBIS_COMMENT",
    CUESHEET: "CUESHEET",
    PICTURE: "PICTURE",
}
# Types 7..126 are reserved for future use.  Type 127 is forbidden so that it
# can never be confused with an audio frame's 0xFF sync byte.
MAX_BLOCK_TYPE = 126


class FlacError(ValueError):
    """Raised when a file is not a conforming FLAC stream."""


def _syncsafe(raw):
    """Decode an ID3v2 syncsafe integer (four bytes, seven bits each)."""
    value = 0
    for byte in raw:
        if byte & 0x80:
            raise FlacError("invalid syncsafe integer in ID3v2 header")
        value = (value << 7) | byte
    return value


def _flac_offset(data):
    """Return the file offset of the ``fLaC`` marker.

    A leading ID3v2 tag is skipped; anything else is rejected.
    """
    if data[:4] == b"fLaC":
        return 0
    if data[:3] != b"ID3" or len(data) < 10:
        raise FlacError("not a FLAC file")
    major = data[3]
    if major > 4:
        raise FlacError("unsupported ID3v2.%d tag" % major)
    flags = data[5]
    tag_size = _syncsafe(data[6:10])  # excludes the 10-byte header
    end = 10 + tag_size
    if flags & 0x10:  # ID3v2.4 footer, 10 bytes, also excluded from tag_size
        end += 10
    if end > len(data) or data[end:end + 4] != b"fLaC":
        raise FlacError("not a FLAC file")
    return end


def blocks(data):
    """Walk the metadata block chain.

    Returns ``(blocks, audio_offset)`` where *blocks* is a list of
    :class:`Block` and *audio_offset* is the file offset at which the audio
    frames begin.  Raises :class:`FlacError` on any truncation, illegal block
    type or missing end-of-chain marker, so audio frames are never parsed as
    metadata.
    """
    start = _flac_offset(data)
    pos = start + 4
    out = []
    while True:
        if pos + 4 > len(data):
            raise FlacError("truncated metadata block header")
        flag_byte = data[pos]
        last = bool(flag_byte & 0x80)
        kind = flag_byte & 0x7F
        if kind > MAX_BLOCK_TYPE:
            raise FlacError("invalid metadata block type %d" % kind)
        length = int.from_bytes(data[pos + 1:pos + 4], "big")
        body_start = pos + 4
        body_end = body_start + length
        if body_end > len(data):
            name = NAMES.get(kind, "reserved type %d" % kind)
            raise FlacError("truncated %s metadata block" % name)
        out.append(Block(kind, body_start, length, data[body_start:body_end]))
        pos = body_end
        if last:
            return out, pos
