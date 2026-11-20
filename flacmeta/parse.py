"""Decoding of the individual metadata blocks, per RFC 9639.

All multi-byte integers are read with the endianness mandated by the block:
STREAMINFO and PICTURE use big endian, while VORBIS_COMMENT uses little
endian (it reuses the Vorbis comment packing).
"""
from .blocks import FlacError

STREAMINFO_LENGTH = 34
SEEKPOINT_LENGTH = 18


def _need(length, actual, name):
    if actual != length:
        raise FlacError("%s block must be %d bytes, got %d" % (name, length, actual))


def _u32be(b, pos):
    return int.from_bytes(b[pos:pos + 4], "big")


def streaminfo(b):
    """Decode the 34-byte STREAMINFO block (RFC 9639 section 8)."""
    _need(STREAMINFO_LENGTH, len(b), "STREAMINFO")
    packed = int.from_bytes(b[10:18], "big")
    sample_rate = (packed >> 44) & 0xFFFFF          # 20 bits
    channels = ((packed >> 41) & 0x7) + 1           # 3 bits, stored minus one
    bits_per_sample = ((packed >> 36) & 0x1F) + 1   # 5 bits, stored minus one
    total_samples = packed & 0xFFFFFFFFF             # 36 bits; zero means unknown
    if sample_rate == 0 or sample_rate > 655350:
        raise FlacError("invalid STREAMINFO sample rate %d" % sample_rate)
    return {
        "min_blocksize": int.from_bytes(b[0:2], "big"),
        "max_blocksize": int.from_bytes(b[2:4], "big"),
        "min_framesize": int.from_bytes(b[4:7], "big"),
        "max_framesize": int.from_bytes(b[7:10], "big"),
        "sample_rate": sample_rate,
        "channels": channels,
        "bits_per_sample": bits_per_sample,
        "total_samples": total_samples,
        "md5": b[18:34].hex(),
    }


def _read_le32(b, pos):
    if pos + 4 > len(b):
        raise FlacError("truncated VORBIS_COMMENT block")
    return int.from_bytes(b[pos:pos + 4], "little"), pos + 4


def _read_text(b, pos, length):
    end = pos + length
    if end > len(b):
        raise FlacError("truncated VORBIS_COMMENT block")
    try:
        return b[pos:end].decode("utf-8"), end
    except UnicodeDecodeError as exc:
        raise FlacError("VORBIS_COMMENT is not valid UTF-8: %s" % exc)


def vorbis_comment(b):
    """Decode a VORBIS_COMMENT block.

    Returns ``(vendor, tags)`` where *tags* maps an upper-case field name to
    the list of values attached to that name, in order of appearance.
    """
    pos = 0
    vendor_len, pos = _read_le32(b, pos)
    vendor, pos = _read_text(b, pos, vendor_len)
    count, pos = _read_le32(b, pos)
    tags = {}
    for _ in range(count):
        size, pos = _read_le32(b, pos)
        text, pos = _read_text(b, pos, size)
        if "=" not in text:
            raise FlacError("Vorbis comment without '=' separator")
        name, value = text.split("=", 1)
        if not name or any(ord(ch) < 0x20 or ord(ch) > 0x7D for ch in name):
            raise FlacError("invalid Vorbis comment field name %r" % name)
        tags.setdefault(name.upper(), []).append(value)
    if pos != len(b):
        raise FlacError("trailing %d bytes after VORBIS_COMMENT" % (len(b) - pos))
    return vendor, tags


def seektable(b):
    """Decode a SEEKTABLE block into (sample_number, offset, samples) tuples."""
    if len(b) % SEEKPOINT_LENGTH:
        raise FlacError(
            "SEEKTABLE length %d is not a multiple of %d" % (len(b), SEEKPOINT_LENGTH))
    points = []
    for i in range(0, len(b), SEEKPOINT_LENGTH):
        points.append((
            int.from_bytes(b[i:i + 8], "big"),
            int.from_bytes(b[i + 8:i + 16], "big"),
            int.from_bytes(b[i + 16:i + 18], "big"),
        ))
    return points


def picture(b):
    """Decode a PICTURE block (RFC 9639 section 9). Every integer is big endian."""
    pos = 0
    kind = _u32be(b, pos)
    pos += 4
    mime_len = _u32be(b, pos)
    pos += 4
    if pos + mime_len > len(b):
        raise FlacError("truncated PICTURE MIME type")
    try:
        mime = b[pos:pos + mime_len].decode("ascii")
    except UnicodeDecodeError as exc:
        raise FlacError("PICTURE MIME type is not ASCII: %s" % exc)
    pos += mime_len
    desc_len = _u32be(b, pos)
    pos += 4
    if pos + desc_len > len(b):
        raise FlacError("truncated PICTURE description")
    try:
        desc = b[pos:pos + desc_len].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FlacError("PICTURE description is not valid UTF-8: %s" % exc)
    pos += desc_len
    if pos + 24 > len(b):
        raise FlacError("truncated PICTURE header")
    width = _u32be(b, pos)
    height = _u32be(b, pos + 4)
    depth = _u32be(b, pos + 8)
    colors = _u32be(b, pos + 12)
    data_len = _u32be(b, pos + 16)
    pos += 20
    if pos + data_len != len(b):
        raise FlacError("PICTURE data length does not match block size")
    return {
        "type": kind,
        "mime": mime,
        "description": desc,
        "width": width,
        "height": height,
        "depth": depth,
        "colors": colors,
        "data_length": data_len,
    }


def padding(b):
    """Validate a PADDING block, which must be entirely zero bytes."""
    if b and any(b):
        raise FlacError("PADDING block contains non-zero bytes")
    return len(b)
