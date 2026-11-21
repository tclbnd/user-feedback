"""Summary of one module file."""
from .module import parse
from .timing import duration


def summary(data):
    m = parse(data)
    sample_bytes = sum(s["length"] for s in m["samples"])
    present = max(0, min(sample_bytes, len(data) - m["sample_data_offset"]))
    return {
        "format": m["format"], "signature": m["signature"], "channels": m["channels"], "title": m["title"],
        "song_length": m["song_length"], "patterns": m["patterns"],
        "samples": [{k: s[k] for k in ("index", "name", "length", "finetune", "volume", "loop_start", "loop_length", "looped")}
                    for s in m["samples"] if s["length"]],
        "sample_bytes": sample_bytes, "sample_bytes_present": present,
        "duration": str(duration(m)),
    }
