"""Decode an ADPCM WAVE file and summarise it."""
import struct
import zlib
from fractions import Fraction

from . import codec
from .wav import WavError, chunks, fmt

FORMATS = {0x0011: "ima_adpcm", 0x0002: "ms_adpcm"}


def decode(data):
    ch = chunks(data)
    if b"fmt " not in ch or b"data" not in ch:
        raise WavError("missing fmt or data chunk")
    f = fmt(ch[b"fmt "])
    if f["tag"] not in FORMATS:
        raise WavError("unsupported format tag 0x%04x" % f["tag"])
    n, align = f["channels"], f["block_align"]
    audio = ch[b"data"]
    if f["tag"] == 0x0011:
        spb, decode_block = codec.ima_samples_per_block(align, n), codec.ima_block
    else:
        spb, decode_block = codec.ms_samples_per_block(align, n), codec.ms_block
    pcm = [[] for _ in range(n)]
    blocks = 0
    for pos in range(0, len(audio) - align + 1, align):
        for c, samples in enumerate(decode_block(audio[pos:pos + align], n)):
            pcm[c].extend(samples)
        blocks += 1
    return f, spb, blocks, pcm


def summary(data):
    f, spb, blocks, pcm = decode(data)
    frames = len(pcm[0]) if pcm else 0
    inter = b"".join(struct.pack("<" + "h" * len(pcm), *(pcm[c][i] for c in range(len(pcm)))) for i in range(frames))
    return {
        "format": FORMATS[f["tag"]],
        "channels": f["channels"],
        "sample_rate": f["sample_rate"],
        "block_align": f["block_align"],
        "samples_per_block": spb,
        "blocks": blocks,
        "frames": frames,
        "duration": str(Fraction(frames, f["sample_rate"])),
        "peak": [max((abs(s) for s in ch), default=0) for ch in pcm],
        "head": [ch[:4] for ch in pcm],
        "pcm_crc32": "%08x" % zlib.crc32(inter),
    }
