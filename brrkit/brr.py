"""SNES BRR samples (.brr): optional loop offset header, 9-byte blocks decoded as the S-DSP does."""


class BrrError(ValueError):
    pass


def parse(data):
    if not data:
        raise BrrError("not a whole number of BRR blocks")

    # Raw BRR files contain only 9-byte blocks.  Ripping tools prefix looped
    # samples with a two-byte little-endian byte offset to the loop block.
    loop_header = len(data) % 9 == 2
    loop_offset = None
    if loop_header:
        loop_offset = data[0] | (data[1] << 8)
        data = data[2:]

    if not data or len(data) % 9:
        raise BrrError("not a whole number of BRR blocks")

    blocks = len(data) // 9
    p1 = p2 = 0
    peak, last, played = 0, None, 0
    ended = False
    looped = False

    for k in range(blocks):
        b = data[9 * k:9 * k + 9]
        shift, filt = b[0] >> 4, (b[0] >> 2) & 3

        for i in range(16):
            nibble = b[1 + i // 2]
            n = (nibble >> 4 if i % 2 == 0 else nibble) & 15
            s = n - 16 if n & 8 else n
            s <<= shift

            # The S-DSP's predictors are fixed-point signed shifts.  Using
            # integer arithmetic here avoids filter-state drift later in a
            # sample.
            if filt == 1:
                s += (p1 * 15) >> 4
            elif filt == 2:
                s += ((p1 * 61) >> 5) - ((p2 * 15) >> 4)
            elif filt == 3:
                s += ((p1 * 115) >> 6) - ((p2 * 13) >> 4)

            s = max(-32768, min(32767, s))
            p2, p1 = p1, s
            peak = max(peak, abs(s))
            last = s

        played += 16

        # The END block is still decoded and played.  Nothing after it is
        # part of this sample's first pass.
        if b[0] & 0x01:
            ended = True
            looped = bool(b[0] & 0x02)
            break

    if loop_header:
        # The prefix stores a byte offset to a 9-byte block header; every
        # block contains 16 samples.
        loop_start = (loop_offset // 9) * 16
    else:
        loop_start = None

    return {"blocks": blocks, "loop_header": loop_header, "samples": played,
            "ended": ended, "looped": looped, "loop_start": loop_start,
            "peak": "%.6f" % (peak / 32768), "last_sample": last}
