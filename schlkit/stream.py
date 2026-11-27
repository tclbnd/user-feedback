"""Electronic Arts SCxl audio streams."""
import zlib

CODECS = {0: "pcm16", 1: "pcm8", 2: "ea-adpcm"}


class SchlError(Exception):
    pass


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _c16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def pcm(buf, codec, channels):
    if codec == "pcm16":
        n = len(buf) // (2 * channels) * channels
        return [_s16(buf[2 * k] | buf[2 * k + 1] << 8) for k in range(n)]
    n = len(buf) // channels * channels
    return [(b - 128) << 8 for b in buf[:n]]


def chunks(data):
    pos = 0
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if pos + 8 + size > len(data):
            raise SchlError("chunk %r runs past the end of the file" % tag.decode("latin-1"))
        yield tag, data[pos + 8:pos + 8 + size]
        pos += 8 + size


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
    if codec == "ea-adpcm":
        raise SchlError("EA ADPCM is not supported yet")
    allv, packets = [], []
    for tag, body in it:
        if tag not in (b"SCCl", b"SCDl", b"SCEl"):
            raise SchlError("unexpected chunk %r" % tag.decode("latin-1"))
        if tag != b"SCDl":
            continue
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
