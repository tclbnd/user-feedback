"""Electronic Arts SCxl audio streams."""
import zlib

CODECS = {0: "pcm16", 1: "pcm8", 2: "ea-adpcm"}

# The EA ADPCM table, as used by the original player: four predictor
# coefficient pairs, then the sixteen companded nibble deltas.
EA_ADPCM_TABLE = (
    0, 240, 460, 392,
    0, 0, -208, -220,
    0, 1, 3, 4, 7, 8, 10, 11,
    0, -1, -3, -4, -7, -8, -10, -11,
)


class SchlError(Exception):
    pass


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _s8(v):
    return v - 0x100 if v & 0x80 else v


def _c16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def pcm(buf, codec, channels):
    if codec == "pcm16":
        n = len(buf) // (2 * channels) * channels
        return [_s16(buf[2 * k] | buf[2 * k + 1] << 8) for k in range(n)]
    n = len(buf) // channels * channels
    return [_s8(b) << 8 for b in buf[:n]]


def ea_adpcm(body):
    """Decode one SCDl packet of EA ADPCM.

    The packet starts with a 12-byte header: the number of sample frames
    (u32 little-endian) and the initial predictor history (two s16 values,
    most recent first) for each channel, left then right.  The rest is
    30-byte pieces: one control byte per channel and 28 bytes holding one
    4-bit delta per channel (high nibble left, low nibble right), 28
    sample frames per piece.

    Returns the interleaved samples, or None when the packet is too
    damaged to decode.
    """
    if len(body) < 12:
        return None
    frames = int.from_bytes(body[0:4], "little")
    hist = [[_s16(body[4] | body[5] << 8), _s16(body[6] | body[7] << 8)],
            [_s16(body[8] | body[9] << 8), _s16(body[10] | body[11] << 8)]]
    data = body[12:]
    if frames > len(data) // 30 * 28:
        return None
    out = []
    for off in range(0, len(data) - 29, 30):
        coef1 = [EA_ADPCM_TABLE[data[off] >> 4], EA_ADPCM_TABLE[data[off + 1] >> 4]]
        coef2 = [EA_ADPCM_TABLE[(data[off] >> 4) + 4], EA_ADPCM_TABLE[(data[off + 1] >> 4) + 4]]
        for k in range(28):
            byte = data[off + 2 + k]
            for ch, nib in enumerate((byte >> 4, byte & 0x0F)):
                prev1, prev2 = hist[ch]
                s = ((coef1[ch] * prev1 + coef2[ch] * prev2) >> 8) + (EA_ADPCM_TABLE[8 + nib] << 8)
                s = _c16(s)
                hist[ch] = [s, prev1]
                out.append(s)
    return out[:frames * 2]


def chunks(data):
    pos = 0
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if size < 8 or pos + size > len(data):
            raise SchlError("chunk %r runs past the end of the file" % tag.decode("latin-1"))
        yield tag, data[pos + 8:pos + size]
        pos += size


def parse(data):
    it = chunks(data)
    first = next(it, None)
    if first is None or first[0] != b"SCHl" or len(first[1]) < 6:
        raise SchlError("stream does not start with a SCHl header")
    head = first[1]
    channels, code = head[0], head[1]
    rate = int.from_bytes(head[2:6], "little")
    if channels not in (1, 2):
        raise SchlError("channels must be 1 or 2")
    if code not in CODECS:
        raise SchlError("unknown codec %d" % code)
    codec = CODECS[code]
    allv, packets = [], []
    for tag, body in it:
        if tag not in (b"SCCl", b"SCDl", b"SCEl"):
            raise SchlError("unexpected chunk %r" % tag.decode("latin-1"))
        if tag != b"SCDl":
            continue
        if codec == "ea-adpcm":
            s = ea_adpcm(body)
            if s is None:
                s = []
        else:
            s = pcm(body, codec, channels)
        packets.append(len(s))
        allv += s
    chans = []
    for k in range(channels):
        v = allv[k::channels]
        raw = b"".join(x.to_bytes(2, "little", signed=True) for x in v)
        chans.append({"samples": len(v), "peak": max((abs(x) for x in v), default=0), "sum": sum(v),
                      "crc32": "%08x" % zlib.crc32(raw)})
    frames = len(allv) // channels
    return {"codec": codec, "channels": channels, "rate": rate, "packets": packets, "frames": frames,
            "duration": round(frames / rate, 4) if rate else None, "channel": chans}
