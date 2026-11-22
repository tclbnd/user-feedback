"""Builders for small synthetic FSB5 banks used by the tests."""
import struct

RATES = (4000, 8000, 11000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000)


def extra(kind, body, more=False):
    return struct.pack("<I", (1 if more else 0) | (len(body) << 1) | (kind << 25)) + body


def sample(rate_index=8, ch_code=1, samples=44100, offset=0, extras=()):
    bits = (1 if extras else 0) | (rate_index << 1) | (ch_code << 5) | ((offset // 32) << 7) | (samples << 34)
    out = struct.pack("<Q", bits)
    for i, (kind, body) in enumerate(extras):
        out += extra(kind, body, more=i + 1 < len(extras))
    return out


def fsb(samples, sizes, version=1, codec=15, names=None):
    cut = 0
    """samples: list of (rate_index, ch_code, nsamples, extras); sizes: data bytes per sample (padded to 32)."""
    offs, o = [], 0
    for sz in sizes:
        offs.append(o)
        o += -(-sz // 32) * 32
    sh = b"".join(sample(r, c, n, offs[i], ex) for i, (r, c, n, ex) in enumerate(samples))
    nt = b""
    if names is not None:
        table = b"".join(struct.pack("<I", 0) for _ in names)
        strings, ptrs = b"", []
        for n in names:
            ptrs.append(4 * len(names) + len(strings))
            strings += n.encode() + b"\0"
        nt = b"".join(struct.pack("<I", p) for p in ptrs) + strings
        nt += bytes(-len(nt) % 16)
    dsize = o
    head = b"FSB5" + struct.pack("<6I", version, len(samples), len(sh), len(nt), dsize, codec)
    head += bytes(12 if version == 0 else 8) + bytes(16) + bytes(8)
    out = head + sh + nt + bytes(dsize)
    return out[:len(out) - cut] if cut else out
