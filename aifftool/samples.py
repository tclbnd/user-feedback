"""Sample decoding and level statistics."""


def frames_in(data, channels, bits):
    width = bits // 8
    return len(data) // (channels * width) if channels and width else 0


def peaks(data, channels, bits, compression):
    width = bits // 8
    count = frames_in(data, channels, bits)
    top = [0] * channels
    for f in range(count):
        for c in range(channels):
            p = (f * channels + c) * width
            raw = data[p:p + width]
            if width == 1:
                value = raw[0] - 128
            else:
                value = int.from_bytes(raw, "big", signed=True)
            top[c] = max(top[c], abs(value))
    full = 2 ** (bits - 1)
    return [round(t / full, 6) for t in top]
