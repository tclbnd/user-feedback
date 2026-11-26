"""Microsoft ADPCM (format tag 2) WAV files: RIFF chunks, fmt coefficients, block decoding."""
import struct
import zlib

ADAPT = [230, 230, 230, 230, 307, 409, 512, 614, 768, 614, 512, 409, 307, 230, 230, 230]
COEF = [(256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232)]


class AdpcmError(ValueError):
    pass


def _chunks(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AdpcmError("not a RIFF WAVE file")
    pos, out = 12, {}
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        out.setdefault(cid, data[pos + 8:pos + 8 + size])
        pos += 8 + size
    return out


def _clamp(v):
    return max(-32768, min(32767, v))


def _block(blk, ch, coefs, spb):
    if len(blk) < 7 * ch:
        return [[] for _ in range(ch)]
    out = []
    heads = []
    for c in range(ch):
        pred, delta, s1, s2 = struct.unpack_from("<Bhhh", blk, 7 * c)
        heads.append([pred, delta, s1, s2])
        out.append([s1, s2])
    nib = []
    for b in blk[7 * ch:]:
        nib += [b & 15, b >> 4]
    want = (spb - 2) * ch
    for i, n in enumerate(nib[:want]):
        c = i % ch
        pred, delta, s1, s2 = heads[c]
        c1, c2 = coefs[pred]
        p = (s1 * c1 + s2 * c2) >> 8
        sn = n - 16 if n & 8 else n
        v = _clamp(p + sn * delta)
        heads[c] = [pred, ADAPT[n] * delta >> 8, v, s1]
        out[c].append(v)
    return out


def parse(data):
    ck = _chunks(data)
    f = ck.get(b"fmt ")
    if f is None or len(f) < 20:
        raise AdpcmError("no fmt chunk")
    tag, ch, rate, _, align, bits, cb, spb, ncoef = struct.unpack_from("<HHIIHHHHH", f)
    if tag != 2:
        raise AdpcmError("not Microsoft ADPCM")
    coefs = COEF
    body = ck.get(b"data", b"")
    chans = [[] for _ in range(ch)]
    blocks = 0
    for off in range(0, len(body), align):
        dec = _block(body[off:off + align], ch, coefs, spb)
        if dec[0]:
            blocks += 1
        for c in range(ch):
            chans[c] += dec[c]
    frames = len(chans[0])
    pcm = b"".join(struct.pack("<%dh" % ch, *(chans[c][i] for c in range(ch))) for i in range(frames))
    return {"channels": ch, "sample_rate": rate, "block_align": align, "samples_per_block": spb, "blocks": blocks,
            "frames": frames, "duration": round(frames / rate, 4) if rate else None,
            "peak": max((abs(v) for c in chans for v in c), default=0),
            "head": [c[:6] for c in chans], "last": [c[-1] if c else None for c in chans],
            "crc32": "%08x" % zlib.crc32(pcm)}
