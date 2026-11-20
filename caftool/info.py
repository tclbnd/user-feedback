"""Summary of one CAF file."""
from . import fields, pcm
from .chunks import CafError, chunks


def summary(data):
    found = chunks(data)
    d = audio = ch = None
    tags = {}
    for kind, body in found:
        if kind == b"desc":
            d = fields.desc(body)
        elif kind == b"data":
            audio = fields.data(body)
        elif kind == b"chan":
            ch = fields.chan(body)
        elif kind == b"info":
            tags = fields.info(body)
    if d is None:
        raise CafError("missing desc chunk")
    if audio is None:
        raise CafError("missing data chunk")
    # The pakt chunk is decoded after desc because the desc chunk's
    # frames-per-packet value decides whether each packet entry also carries a
    # frame count.
    pk = None
    for kind, body in found:
        if kind == b"pakt":
            pk = fields.pakt(body, d["frames_per_packet"])

    samples = audio["audio"]
    fpp = d["frames_per_packet"]
    bpp = d["bytes_per_packet"]
    if pk is not None:
        packets = pk["packets"]
        # mNumberValidFrames is authoritative: it already excludes the priming
        # and remainder frames a player drops.  Fall back to deriving it from
        # the packet table (or the fixed packet size) if it is unusable.
        if pk["valid_frames"] > 0:
            frames = pk["valid_frames"]
        else:
            total = sum(pk["counts"]) if pk["counts"] else packets * fpp
            frames = total - pk["priming"] - pk["remainder"]
    else:
        packets = len(samples) // bpp if bpp else 0
        frames = packets * fpp
    packets = max(packets, 0)
    frames = max(frames, 0)

    rate = d["sample_rate"]
    lpcm = d["format_id"] == "lpcm"
    return {
        "format_id": d["format_id"],
        "sample_rate": rate,
        "channels": d["channels"],
        "bits": d["bits"],
        "bytes_per_packet": bpp,
        "frames_per_packet": fpp,
        "packets": packets,
        "frames": frames,
        "duration": round(frames / rate, 6) if rate else None,
        "peak": pcm.peaks(samples, d["channels"], d["bits"], d["format_flags"],
                          bpp, fpp) if lpcm else None,
        "layout_channels": ch["channels"] if ch else None,
        "info": tags,
    }
