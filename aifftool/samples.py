"""Sample decoding and level statistics."""
import struct

from .chunks import AiffError

# AIFF-C compression types we can decode: NONE and twos are big-endian
# two's complement, sowt is little-endian two's complement, fl32 is
# big-endian 32-bit float.
_SUPPORTED = ("NONE", "twos", "sowt", "fl32")


def width_of(bits):
    """Bytes per sample point: samples occupy whole bytes, left-justified."""
    return (bits + 7) // 8


def frames_in(data, channels, bits):
    width = width_of(bits)
    return len(data) // (channels * width) if channels and width else 0


def peaks(data, channels, bits, compression):
    if compression not in _SUPPORTED:
        raise AiffError("unsupported compression type %r" % compression)
    width = width_of(bits)
    count = frames_in(data, channels, bits)
    little = compression == "sowt"
    floating = compression == "fl32"
    top = [0] * channels
    for f in range(count):
        for c in range(channels):
            p = (f * channels + c) * width
            raw = data[p:p + width]
            if floating:
                value = struct.unpack(">f", raw)[0]
            else:
                value = int.from_bytes(raw, "little" if little else "big",
                                       signed=True)
            top[c] = max(top[c], abs(value))
    if floating:
        return [round(t, 6) for t in top]
    # Integer samples are left-justified in their container bytes, so the
    # container's full scale normalises any bit depth correctly.
    full = 1 << (width * 8 - 1)
    return [round(t / full, 6) for t in top]
