"""Westwood Studios audio (.aud): header and chunk headers."""
import struct
from fractions import Fraction

CODECS = {1: "westwood_adpcm"}


class AudError(ValueError):
    pass


def parse(data):
    if len(data) < 12:
        raise AudError("not a Westwood AUD file")
    rate, out_size, size, flags, codec = struct.unpack_from("<HIIBB", data)
    if rate == 0:
        raise AudError("not a Westwood AUD file")
    channels, bits = (2 if flags & 2 else 1), (16 if flags & 1 else 8)
    p, end = 12, len(data)
    chunks = bad = in_bytes = out_bytes = 0
    while p < end:
        csize, cout, cid = struct.unpack_from(">HHI", data, p)
        if cid != 0x0000DEAF:
            bad += 1
        chunks += 1
        in_bytes += csize
        out_bytes += cout
        p += 8 + csize
    frames = out_size // (channels * bits // 8)
    return {"sample_rate": rate, "channels": channels, "bits": bits, "codec": CODECS.get(codec, "type_%d" % codec),
            "chunks": chunks, "bad_chunk_ids": bad, "data_bytes": in_bytes, "header_size_ok": in_bytes + 8 * chunks == size,
            "output_bytes": out_size, "chunk_output_bytes": out_bytes, "frames": frames,
            "duration": str(Fraction(frames, rate)), "truncated": False, "peak": None, "last_sample": None}
