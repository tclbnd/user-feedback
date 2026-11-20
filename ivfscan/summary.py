"""Clip summary for the lab dashboard."""
from collections import Counter

from . import ivf, obu, seqhdr


def summarise(data):
    head, frames = ivf.read(data)
    counts, seq, keys = Counter(), None, 0
    for fr in frames:
        for o in obu.obus(fr.data):
            counts[obu.NAMES.get(o.type, str(o.type))] += 1
            if o.type == 1 and seq is None:
                seq = seqhdr.parse(o.payload)
            if o.type in (3, 6) and o.payload and (o.payload[0] >> 5) == 0:
                keys += 1
    return {**head, "frames": len(frames), "first_pts": frames[0].pts if frames else None,
            "last_pts": frames[-1].pts if frames else None, "keyframes": keys,
            "obu_counts": dict(sorted(counts.items())), "sequence": seq}
