"""Decoding of the individual metadata blocks per RFC 9639.

All decoders raise :class:`~flacmeta.blocks.FlacError` when a block body is
truncated or malformed: silently replacing bad bytes would put wrong values on
the release sheet.
"""
from .blocks import FlacError

STREAMINFO_SIZE = 34
SEEKPOINT_SIZE = 18
# 36-bit field, so this value means "total sample count unknown".
TOTAL_SAMPLES_UNKNOWN = (1 << 36) - 1
MAX_CHANNELS = 8
MAX_BITS_PER_SAMPLE = 32


class _Reader:
    """Cursor over a block body with bounds-checked integer reads."""

    def __init__(self, body):
        self.body = body
        self.pos = 0

    def take(self, size):
        end = self.pos + size
        if end > len(self.body):
            raise FlacError("truncated metadata block body")
        chunk = self.body[self.pos:end]
        self.pos = end
        return chunk

    def u32le(self):
        return int.from_bytes(self.take(4), "little")

    def u32be(self):
        return int.from_bytes(self.take(4), "big")

    def text(self, size, encoding):
        try:
            return self.take(size).decode(encoding)
        except UnicodeDecodeError as exc:
            raise FlacError("invalid %s text in metadata block" % encoding) from exc


def streaminfo(b):
    if len(b) != STREAMINFO_SIZE:
        raise FlacError("STREAMINFO must be %d bytes, got %d" % (STREAMINFO_SIZE, len(b)))
    packed = int.from_bytes(b[10:18], "big")
    sample_rate = packed >> 44
    channels = ((packed >> 41) & 0x7) + 1          # stored as channels minus one
    bits_per_sample = ((packed >> 36) & 0x1F) + 1  # stored as depth minus one
    total_samples = packed & ((1 << 36) - 1)       # full 36-bit field
    if not 1 <= channels <= MAX_CHANNELS:
        raise FlacError("invalid channel count %d in STREAMINFO" % channels)
    if not 4 <= bits_per_sample <= MAX_BITS_PER_SAMPLE:
        raise FlacError("invalid sample depth %d in STREAMINFO" % bits_per_sample)
    if sample_rate == 0:
        raise FlacError("invalid sample rate 0 in STREAMINFO")
    return {
        "min_blocksize": int.from_bytes(b[0:2], "big"),
        "max_blocksize": int.from_bytes(b[2:4], "big"),
        "min_framesize": int.from_bytes(b[4:7], "big"),
        "max_framesize": int.from_bytes(b[7:10], "big"),
        "sample_rate": sample_rate,
        "channels": channels,
        "bits_per_sample": bits_per_sample,
        # None means the encoder did not record a total; callers must not
        # invent a duration from it.
        "total_samples": None if total_samples == TOTAL_SAMPLES_UNKNOWN else total_samples,
        "md5": b[18:34].hex(),
    }


def vorbis_comment(b):
    """Decode a VORBIS_COMMENT block.

    Lengths are 32-bit little-endian.  Returns ``(vendor, tags)`` where tags
    maps each upper-cased field name to a list of its values, ordered by
    appearance so repeated fields (e.g. two ARTIST values) are all retained.
    """
    r = _Reader(b)
    vendor_length = r.u32le()
    vendor = r.text(vendor_length, "utf-8")
    count = r.u32le()
    tags = {}
    for _ in range(count):
        length = r.u32le()
        entry = r.text(length, "utf-8")
        if "=" not in entry:
            raise FlacError("Vorbis comment without '=': %r" % entry[:32])
        name, value = entry.split("=", 1)
        if not name or not all(0x20 <= ord(c) <= 0x7D and c != "=" for c in name):
            raise FlacError("invalid Vorbis comment field name: %r" % name[:32])
        tags.setdefault(name.upper(), []).append(value)
    if r.pos != len(b):
        raise FlacError("trailing bytes after VORBIS_COMMENT")
    return vendor, tags


def seektable(b):
    if len(b) % SEEKPOINT_SIZE:
        raise FlacError("SEEKTABLE body is not a whole number of 18-byte points")
    points = []
    for i in range(0, len(b), SEEKPOINT_SIZE):
        points.append((int.from_bytes(b[i:i + 8], "big"),
                       int.from_bytes(b[i + 8:i + 16], "big"),
                       int.from_bytes(b[i + 16:i + 18], "big")))
    return points


def picture(b):
    """Decode a PICTURE block. Every integer in it is 32-bit big-endian."""
    r = _Reader(b)
    kind = r.u32be()
    mime_length = r.u32be()
    mime = r.text(mime_length, "ascii")
    desc_length = r.u32be()
    description = r.text(desc_length, "utf-8")
    width = r.u32be()
    height = r.u32be()
    depth = r.u32be()
    colors = r.u32be()
    data_length = r.u32be()
    picture_data = r.take(data_length)
    if r.pos != len(b):
        raise FlacError("trailing bytes after PICTURE")
    return {"type": kind, "mime": mime, "description": description,
            "width": width, "height": height, "depth": depth, "colors": colors,
            "data_length": len(picture_data)}
