"""Westwood SND1 audio (.snd): chunks of 2-bit and 4-bit ADPCM, raw bytes, big deltas and runs, 8-bit unsigned output."""
import zlib

# 4-bit ADPCM step table used by the game's sound driver. The high nibble of
# a byte is played second, the low nibble first.
TABLE4 = (-9, -8, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 8)

SILENCE = 128


class SndError(ValueError):
    pass


def clip(v):
    return 0 if v < 0 else 255 if v > 255 else v


def chunk(buf):
    if len(buf) < 4:
        raise SndError("chunk is too small")
    out_size = buf[0] | buf[1] << 8
    in_size = buf[2] | buf[3] << 8
    if in_size > len(buf):
        raise SndError("chunk data is larger than the chunk")
    p = 4
    # The driver passes coded blocks through untouched when both sizes agree.
    if in_size == out_size:
        return bytes(buf[p:p + out_size])
    out = bytearray()
    # Every block starts from silence; no predictor carries over between
    # chunks the way the driver restarts its output sample.
    sample = SILENCE
    while len(out) < out_size and p < len(buf):
        code, count = buf[p] >> 6, buf[p] & 0x3F
        p += 1
        if code == 0:
            # 2-bit ADPCM: each byte holds four samples, low pair first
            # (the driver shifts right by 0, 2, 4 and 6).
            for _ in range(count + 1):
                if p >= len(buf):
                    raise SndError("2-bit ADPCM block is truncated")
                c = buf[p]
                p += 1
                for sh in (0, 2, 4, 6):
                    sample = clip(sample + ((c >> sh) & 3) - 2)
                    out.append(sample)
        elif code == 1:
            # 4-bit ADPCM: low nibble first, then the high nibble, with the
            # signed delta taken from the driver's step table.
            for _ in range(count + 1):
                if p >= len(buf):
                    raise SndError("4-bit ADPCM block is truncated")
                c = buf[p]
                p += 1
                sample = clip(sample + TABLE4[c & 0xF])
                out.append(sample)
                sample = clip(sample + TABLE4[c >> 4])
                out.append(sample)
        elif code == 2:
            if count & 0x20:
                # One big delta: an unsigned five-bit jump upward (0..31).
                sample = clip(sample + (count & 0x1F))
                out.append(sample)
            else:
                # Raw bytes, copied verbatim; each byte is the new sample.
                if p + count + 1 > len(buf):
                    raise SndError("raw block is truncated")
                for _ in range(count + 1):
                    sample = buf[p]
                    p += 1
                    out.append(sample)
        else:
            # Run: hold the current sample.
            out += bytes([sample]) * (count + 1)
    return bytes(out)


def parse(data):
    if len(data) < 12 or data[:8] != b"SND1PACK":
        raise SndError("not an SND1 pack")
    rate = data[8] | data[9] << 8
    count = data[10] | data[11] << 8
    pos = 12
    samples, chunks = bytearray(), []
    for _ in range(count):
        if pos + 4 > len(data):
            raise SndError("chunk table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise SndError("chunk is truncated")
        pos += 4 + n
        try:
            s = chunk(body)
            chunks.append(len(s))
            samples += s
        except SndError:
            chunks.append(None)
    signed = [v - 128 for v in samples]
    return {"rate": rate, "chunks": chunks, "samples": len(samples),
            "duration": round(len(samples) / rate, 4) if rate else None,
            "peak": max((abs(v) for v in signed), default=0), "sum": sum(signed),
            "crc32": "%08x" % zlib.crc32(bytes(samples))}
