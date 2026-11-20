"""Linear PCM sample statistics.

The Audio Description chunk describes linear PCM with the format flags from
the Core Audio Format specification.  Note that CAF defines its own flags,
which differ from the ``AudioFormatFlags`` in CoreAudioTypes.h - in
particular the endianness bit means *little* endian (in Core Audio runtime
flags it means big endian), so flags of zero describe big-endian samples:

    kCAFLinearPCMFormatFlagIsFloat        = 1 << 0
    kCAFLinearPCMFormatFlagIsLittleEndian = 1 << 1

Integer samples are stored as two's-complement values; when ``bits`` is not a
whole number of bytes the valid bits are left justified in the container.
"""

import struct

FLAG_IS_FLOAT = 1 << 0
FLAG_IS_LITTLE_ENDIAN = 1 << 1


def _container_bytes(bits, bytes_per_packet, frames_per_packet, channels):
    """Byte width of a single sample, including any unused padding bits."""
    need = max(1, (bits + 7) // 8)
    if bytes_per_packet and frames_per_packet and channels:
        span = frames_per_packet * channels
        if bytes_per_packet % span == 0:
            width = bytes_per_packet // span
            if width >= need:
                return width
    return need


def peaks(data, channels, bits, flags, bytes_per_packet=0, frames_per_packet=0):
    """Per-channel peak, as the largest absolute sample relative to full scale."""
    if not channels or bits <= 0:
        return None
    width = _container_bytes(bits, bytes_per_packet, frames_per_packet, channels)
    little = bool(flags & FLAG_IS_LITTLE_ENDIAN)
    order = "little" if little else "big"
    floating = bool(flags & FLAG_IS_FLOAT)

    if floating:
        fmt = {4: "f", 8: "d"}.get(width)
        if fmt is None:
            return None
        unpack = struct.Struct(("<" if little else ">") + fmt).unpack_from
    else:
        if width not in (1, 2, 3, 4, 8):
            return None
        container_bits = width * 8
        shift = container_bits - bits
        full = float(1 << (bits - 1))

    frame_bytes = width * channels
    count = len(data) // frame_bytes
    top = [0.0] * channels
    for frame in range(count):
        base = frame * frame_bytes
        for ch in range(channels):
            offset = base + ch * width
            if floating:
                value = abs(unpack(data, offset)[0])
            else:
                # Read the whole container as a signed value, then drop the
                # padding low bits so left-justified samples line up.
                raw = int.from_bytes(data[offset:offset + width], order,
                                     signed=True) >> shift
                value = abs(raw) / full
            if value > top[ch]:
                top[ch] = value
    return [round(v, 6) for v in top]
