"""CRI HCA audio headers: chunk list, frame layout, loop points, cipher, header checksum."""
import struct
from fractions import Fraction

SAMPLES_PER_FRAME = 1024
CIPHERS = {0: "none", 1: "keyless", 56: "keyless"}
FIXED = {"fmt": 12, "comp": 12, "dec": 8, "vbr": 4, "ath": 2, "loop": 12, "ciph": 2, "rva": 4}


class HcaError(ValueError):
    pass


def _name(raw):
    return raw.rstrip(b"\0").decode("latin-1"), False


def parse(data):
    if len(data) < 8:
        raise HcaError("not an HCA file")
    name, masked = _name(data[:4])
    if name != "HCA":
        raise HcaError("not an HCA file")
    version, hsize = struct.unpack_from(">HH", data, 4)
    if hsize > len(data) or hsize < 10:
        raise HcaError("header runs past the end of the file")
    out = {"version": "%d.%d" % (version >> 8, version & 0xFF), "masked": masked,
           "header_crc_ok": True, "comment": None, "volume": 1.0, "cipher": "none",
           "loop": None, "vbr": False}
    pos, chunks = 8, {}
    while pos + 4 <= hsize - 2:
        name, m = _name(data[pos:pos + 4])
        out["masked"] = out["masked"] or m
        body = pos + 4
        if name == "pad":
            break
        if name == "comm":
            n = data[body]
            out["comment"] = data[body + 1:body + 1 + n].split(b"\0")[0].decode("utf-8", "replace")
            pos = body + 1 + n
            continue
        if name not in FIXED:
            raise HcaError("unknown chunk %r at %d" % (name, pos))
        chunks[name] = body
        pos = body + FIXED[name]
    if "fmt" not in chunks:
        raise HcaError("no fmt chunk")
    f = chunks["fmt"]
    channels = data[f]
    rate, = struct.unpack_from(">I", data, f)
    blocks, delay, padding = struct.unpack_from(">IHH", data, f + 4)
    if "comp" not in chunks:
        raise HcaError("no comp chunk")
    bsize, = struct.unpack_from(">H", data, chunks["comp"])
    if bsize == 0 and "vbr" in chunks:
        out["vbr"] = True
    ath = struct.unpack_from(">H", data, chunks["ath"])[0] if "ath" in chunks else 0
    if "loop" in chunks:
        ls, le, sd, ep = struct.unpack_from(">IIHH", data, chunks["loop"])
        start = ls * SAMPLES_PER_FRAME
        end = le * SAMPLES_PER_FRAME
        out["loop"] = {"start": start, "end": end}
    if "ciph" in chunks:
        c, = struct.unpack_from(">H", data, chunks["ciph"])
        out["cipher"] = CIPHERS.get(c, str(c))
    if "rva" in chunks:
        out["volume"] = struct.unpack_from(">I", data, chunks["rva"])[0]
    samples = blocks * SAMPLES_PER_FRAME
    out.update(channels=channels, sample_rate=rate, blocks=blocks, block_size=bsize or None,
               encoder_delay=delay, padding=padding, samples=samples,
               duration=str(Fraction(samples, rate)) if rate else None, ath_type=ath,
               truncated=blocks * bsize > len(data))
    return out
