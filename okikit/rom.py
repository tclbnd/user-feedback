"""OKI MSM6295 sample ROMs (.oki): phrase table and 4-bit OKI ADPCM phrases."""

# Step table of the MSM6295 ADPCM decoder: 49 entries, one per step index.
STEPS = [16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
         80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
         337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166,
         1282, 1411, 1552]
ADJ = [-1, -1, -1, -1, 2, 4, 6, 8]

PHRASES = 128          # one 8-byte entry per phrase
TABLE_SIZE = 8 * PHRASES
SAMPLE_MAX = 2047      # the chip's accumulator saturates to signed 12 bits
SAMPLE_MIN = -2048


class RomError(ValueError):
    pass


def _decode(data):
    """Decode one phrase from the chip's reset state.

    The MSM6295 plays the high nibble of each byte first and saturates the
    accumulator to a signed 12-bit sample.
    """
    signal = step = 0
    samples = peak = last = 0
    for byte in data:
        for nibble in (byte >> 4, byte & 15):
            delta = STEPS[step] >> 3
            if nibble & 4:
                delta += STEPS[step]
            if nibble & 2:
                delta += STEPS[step] >> 1
            if nibble & 1:
                delta += STEPS[step] >> 2
            signal = signal - delta if nibble & 8 else signal + delta
            if signal > SAMPLE_MAX:
                signal = SAMPLE_MAX
            elif signal < SAMPLE_MIN:
                signal = SAMPLE_MIN
            step = max(0, min(48, step + ADJ[nibble & 7]))
            peak = max(peak, abs(signal))
            last = signal
            samples += 1
    return samples, peak, last


def parse(data):
    if len(data) < TABLE_SIZE:
        raise RomError("ROM too small for a phrase table")
    out = []
    for num in range(PHRASES):
        e = data[8 * num:8 * num + 8]
        start = e[0] << 16 | e[1] << 8 | e[2]
        end = e[3] << 16 | e[4] << 8 | e[5]
        if start == 0 or end == 0:
            continue  # slot the table does not define: cannot be played
        item = {"phrase": num, "start": "$%05X" % start, "end": "$%05X" % end, "bytes": None, "samples": None,
                "peak": None, "last_sample": None, "valid": True}
        if end < start or end >= len(data):
            item["valid"] = False
        else:
            n, peak, last = _decode(data[start:end + 1])
            item.update(bytes=end - start + 1, samples=n, peak="%.6f" % (peak / 2048), last_sample=last)
        out.append(item)
    return {"size": len(data), "phrases": len(out), "phrase_list": out}
