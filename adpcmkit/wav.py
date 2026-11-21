"""RIFF/WAVE container and fmt chunk."""
import struct


class WavError(ValueError):
    pass


def chunks(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise WavError("not a WAVE file")
    pos, out = 12, {}
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        out.setdefault(cid, data[pos + 8:pos + 8 + size])
        pos += 8 + size + (size & 1)
    return out


def fmt(body):
    tag, channels, rate, avg, align, bits = struct.unpack("<HHIIHH", body[:16])
    out = {"tag": tag, "channels": channels, "sample_rate": rate, "block_align": align, "bits": bits}
    if len(body) >= 20:
        out["samples_per_block"] = struct.unpack("<H", body[18:20])[0]
    return out
