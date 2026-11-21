"""IFF 8SVX instruments."""
import struct
from fractions import Fraction


class SvxError(ValueError):
    pass


def chunks(data):
    if data[:4] != b"FORM" or data[8:12] != b"8SVX":
        raise SvxError("not an IFF 8SVX file")
    end = min(len(data), 8 + int.from_bytes(data[4:8], "big"))
    pos, out = 12, []
    while pos + 8 <= end:
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "big")
        out.append((cid, data[pos + 8:pos + 8 + size]))
        pos += 8 + size
    return out


def summary(data):
    vhdr = body = None
    name = author = None
    annos = []
    for cid, b in chunks(data):
        if cid == b"VHDR":
            vhdr = b
        elif cid == b"BODY":
            body = b
        elif cid == b"NAME":
            name = b.split(b"\0")[0].decode("latin-1")
        elif cid == b"AUTH":
            author = b.split(b"\0")[0].decode("latin-1")
        elif cid == b"ANNO":
            annos = [b.split(b"\0")[0].decode("latin-1")]
    if vhdr is None or body is None:
        raise SvxError("missing VHDR or BODY")
    one, rep, _cyc, rate, octaves, comp, vol = struct.unpack(">IIIHBBI", vhdr[:20])
    samples = [b - 256 if b > 127 else b for b in body]
    return {
        "name": name, "author": author, "annotations": annos,
        "sample_rate": rate, "octaves": octaves, "one_shot": one, "repeat": rep,
        "samples_per_octave": [one + rep], "compression": {0: "none", 1: "fibonacci"}.get(comp, "unknown"),
        "volume": vol, "channels": 1, "channel": "mono",
        "frames": len(samples), "duration": str(Fraction(one + rep, rate)) if rate else None,
        "peak": [max((abs(s) for s in samples), default=0)],
        "head": [samples[:4]],
    }
