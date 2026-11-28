"""Westwood SND1 audio (.snd): chunks of 2-bit and 4-bit ADPCM, raw bytes, big deltas and runs, 8-bit unsigned output."""
import zlib

TABLE4 = (-9, -8, -6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 8)


class SndError(ValueError):
    pass


def clip(v):
    return 0 if v < 0 else 255 if v > 255 else v


_last = 128


def chunk(buf):
    if len(buf) < 4:
        raise SndError("chunk is too small")
    out_size = buf[0] | buf[1] << 8
    in_size = buf[2] | buf[3] << 8
    if in_size > len(buf):
        raise SndError("chunk data is larger than the chunk")
    p = 4
    if in_size == out_size + 4:
        return bytes(buf[p:p + out_size])
    out = bytearray()
    global _last
    sample = _last
    while len(out) < out_size and p < len(buf):
        code, count = buf[p] >> 6, buf[p] & 0x3F
        p += 1
        if code == 0:
            smp = 4 * (count + 1)
        elif code == 1:
            smp = 2 * (count + 1)
        elif code == 2:
            smp = 1 if count & 0x20 else count + 1
        else:
            smp = count + 1
        smp = min(smp, out_size - len(out))
        size = 0 if (code == 2 and count & 0x20) or code == 3 else count + 1
        if p + size > len(buf):
            break
        if code == 0:
            for _ in range(count + 1):
                c = buf[p]
                p += 1
                for sh in (6, 4, 2, 0):
                    sample = clip(sample + ((c >> sh) & 3) - 2)
                    out.append(sample)
        elif code == 1:
            for _ in range(count + 1):
                c = buf[p]
                p += 1
                for nib in (c & 0xF, c >> 4):
                    sample = clip(sample + nib - 8)
                    out.append(sample)
        elif code == 2:
            if count & 0x20:
                d = count & 0x1F
                sample = clip(sample + d)
                out.append(sample)
            else:
                out += buf[p:p + smp]
                p += smp
        else:
            out += bytes([sample]) * smp
    _last = sample
    return bytes(out)


def parse(data):
    if len(data) < 12 or data[:8] != b"SND1PACK":
        raise SndError("not an SND1 pack")
    rate = data[8] | data[9] << 8
    count = data[10] | data[11] << 8
    global _last
    _last = 128
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
