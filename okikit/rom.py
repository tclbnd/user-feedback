"""OKI MSM6295 sample ROMs (.oki): phrase table and 4-bit OKI ADPCM phrases."""

STEPS = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88,
         97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724]
ADJ = [-1, -1, -1, -1, 2, 4, 6, 8]


class RomError(ValueError):
    pass


def _decode(data):
    sig, idx, peak, last, n = 0, 0, 0, 0, 0
    for b in data:
        for nib in (b & 15, b >> 4):
            step = STEPS[idx]
            delta = step >> 3
            if nib & 4:
                delta += step
            if nib & 2:
                delta += step >> 1
            if nib & 1:
                delta += step >> 2
            sig = sig - delta if nib & 8 else sig + delta
            idx = max(0, min(48, idx + ADJ[nib & 7]))
            peak = max(peak, abs(sig))
            last = sig
            n += 1
    return n, peak, last


def parse(data):
    if len(data) < 0x400:
        raise RomError("ROM too small for a phrase table")
    out = []
    for num in range(128):
        e = data[8 * num:8 * num + 8]
        start = e[0] | e[1] << 8 | e[2] << 16
        end = e[3] | e[4] << 8 | e[5] << 16
        if start == 0 and end == 0:
            continue
        item = {"phrase": num, "start": "$%05X" % start, "end": "$%05X" % end, "bytes": None, "samples": None,
                "peak": None, "last_sample": None, "valid": True}
        if end < start or end > len(data):
            item["valid"] = False
        else:
            n, peak, last = _decode(data[start:end])
            item.update(bytes=end - start, samples=n, peak="%.6f" % (peak / 2048), last_sample=last)
        out.append(item)
    return {"size": len(data), "phrases": len(out), "phrase_list": out}
