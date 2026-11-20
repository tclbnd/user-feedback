"""Summary of one AIFF / AIFF-C file."""
from . import fields, samples
from .chunks import AiffError, form


def summary(data):
    form_type, chunks = form(data)
    aifc = form_type == b"AIFC"
    info = None
    sound = None
    marks = []
    for cid, body in chunks:
        if cid == b"COMM":
            info = fields.comm(body, aifc)
        elif cid == b"SSND":
            if info is None:
                raise AiffError("SSND before COMM")
            _, _, sound = fields.ssnd(body)
        elif cid == b"MARK":
            marks = fields.markers(body)
    if info is None:
        raise AiffError("missing COMM chunk")
    if sound is None:
        raise AiffError("missing SSND chunk")
    rate = info["sample_rate"]
    for m in marks:
        m["seconds"] = round(m["position"] / rate, 6) if rate else None
    return {
        "format": "AIFF-C" if aifc else "AIFF",
        "channels": info["channels"],
        "bits": info["bits"],
        "sample_rate": rate,
        "compression": info["compression"],
        "compression_name": info["compression_name"],
        "frames": info["frames"],
        "frames_in_file": samples.frames_in(sound, info["channels"], info["bits"]),
        "duration": round(info["frames"] / rate, 6) if rate else None,
        "peak": samples.peaks(sound, info["channels"], info["bits"], info["compression"]),
        "markers": marks,
    }
