"""Linear PCM sample statistics."""
import struct


def peaks(data, channels, bits, flags):
    width = bits // 8
    fmt = {1: ">b", 2: ">h", 4: ">i"}.get(width)
    if fmt is None or not channels:
        return None
    count = len(data) // (width * channels)
    top = [0] * channels
    for i in range(count * channels):
        v = struct.unpack_from(fmt, data, i * width)[0]
        top[i % channels] = max(top[i % channels], abs(v))
    return [round(t / 2 ** (bits - 1), 6) for t in top]
