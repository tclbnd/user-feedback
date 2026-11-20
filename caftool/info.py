"""Summary of one CAF file."""
from . import fields, pcm
from .chunks import CafError, chunks


def summary(data):
    found = chunks(data)
    d = audio = pk = ch = None
    tags = {}
    for kind, body in found:
        if kind == b"desc":
            d = fields.desc(body)
        elif kind == b"data":
            audio = body
        elif kind == b"pakt":
            pk = fields.pakt(body)
        elif kind == b"chan":
            ch = fields.chan(body)
        elif kind == b"info":
            tags = fields.info(body)
    if d is None:
        raise CafError("missing desc chunk")
    if audio is None:
        raise CafError("missing data chunk")
    if d["bytes_per_packet"]:
        packets = len(audio) // d["bytes_per_packet"]
    else:
        packets = pk["packets"] if pk else 0
    frames = packets * d["frames_per_packet"]
    rate = d["sample_rate"]
    lpcm = d["format_id"] == "lpcm"
    return {
        "format_id": d["format_id"],
        "sample_rate": rate,
        "channels": d["channels"],
        "bits": d["bits"],
        "bytes_per_packet": d["bytes_per_packet"],
        "frames_per_packet": d["frames_per_packet"],
        "packets": packets,
        "frames": frames,
        "duration": round(frames / rate, 6) if rate else None,
        "peak": pcm.peaks(audio, d["channels"], d["bits"], d["format_flags"]) if lpcm else None,
        "layout_channels": ch["descriptions"] if ch else None,
        "info": tags,
    }
