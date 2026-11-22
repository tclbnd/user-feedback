"""Nintendo BRSTM streams: file header, HEAD (stream, track and channel info), ADPC, DATA."""
import struct
from fractions import Fraction

CODECS = {0: "pcm16", 1: "pcm8", 2: "dsp-adpcm"}


class RstmError(ValueError):
    pass


class Reader:
    def __init__(self, data, endian):
        self.d, self.e = data, endian

    def u(self, fmt, pos):
        try:
            return struct.unpack_from(self.e + fmt, self.d, pos)
        except struct.error:
            raise RstmError("header runs past the end of the file at %d" % pos)


def _loop(r, chunks, loop, start, rate, bsamples, blocks, channels, codec, loop_ps):
    if not loop:
        return None
    out = {"start": start, "start_seconds": str(Fraction(start, rate)), "block": start // bsamples if bsamples else None}
    if codec == 2:
        out["predictor_scale"] = ["0x%02x" % p for p in loop_ps]
        base = chunks["ADPC"][0] + 8
        out["history"] = [list(r.u("hh", base + (c * blocks + out["block"]) * 4)) for c in range(channels)]
    return out


def parse(data):
    if data[:4] != b"RSTM" or len(data) < 16:
        raise RstmError("not a BRSTM file")
    r = Reader(data, ">")
    version, size, hsize, nchunks = r.u("HIHH", 6)
    chunks = {}
    for i, name in enumerate(("HEAD", "ADPC", "DATA")[:nchunks]):
        chunks[name] = r.u("II", 16 + i * 8)
    if "HEAD" not in chunks:
        raise RstmError("no HEAD chunk")
    head = chunks["HEAD"][0] + 8

    def ref(pos):
        marker, _, _, off = r.u("BBHI", pos)
        return head + off
    stream, tracks_at, chans_at = ref(head), ref(head + 8), ref(head + 16)
    codec, loop, channels, _, rate = r.u("BBBBI", stream)
    loop_start, total, data_off, blocks, bsize, bsamples, last_size, last_samples, last_padded = r.u("9I", stream + 8)
    ntracks, ttype = r.u("BB", tracks_at)
    tracks = []
    for i in range(ntracks):
        t = ref(tracks_at + 4 + i * 8)
        vol = pan = None
        n, = r.u("B", t)
        tracks.append({"channels": list(data[t + 1:t + 1 + n]), "volume": vol, "pan": pan})
    nch, = r.u("B", chans_at)
    hist = []
    for i in range(nch):
        info = ref(chans_at + 4 + i * 8)
        if codec == 2:
            ps, = r.u("H", info + 34)
            hist.append(ps)
    loop_ps = hist
    need = blocks * bsize * channels
    have = (chunks["DATA"][0] + chunks["DATA"][1] - data_off) if "DATA" in chunks else 0
    return {"version": "%d.%d" % (version >> 8, version & 0xFF), "codec": CODECS.get(codec, str(codec)),
            "channels": channels, "sample_rate": rate, "total_samples": total,
            "duration": str(Fraction(blocks * bsamples, rate)) if rate else None,
            "loop": _loop(r, chunks, loop_start, loop_start, rate, bsamples, blocks, channels, codec, loop_ps),
            "blocks": blocks, "block_size": bsize, "samples_per_block": bsamples,
            "last_block_samples": last_samples, "tracks": tracks,
            "initial_predictor_scale": ["0x%02x" % p for p in hist] if codec == 2 else None,
            "has_adpc": "ADPC" in chunks, "data_bytes_expected": need,
            "truncated": have < need or data_off + need > len(data)}
