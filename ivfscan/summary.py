"""Clip summary for the lab dashboard."""
from collections import Counter

from . import ivf, obu, seqhdr


def _is_key_frame(payload):
    """uncompressed_header(): show_existing_frame == 0 then frame_type == KEY_FRAME."""
    return bool(payload) and payload[0] >> 7 == 0 and (payload[0] >> 5) & 0x3 == 0


def summarise(data):
    head, frames = ivf.read(data)
    counts, seq, keys = Counter(), None, 0
    for fr in frames:
        key = False
        for o in obu.obus(fr.data):
            counts[obu.NAMES.get(o.type, str(o.type))] += 1
            if o.type == 1 and seq is None:
                seq = seqhdr.parse(o.payload)
            # OBU_FRAME_HEADER and OBU_FRAME both carry the frame header.
            if o.type in (3, 6) and _is_key_frame(o.payload):
                key = True
        # A still picture sequence only contains key frames.
        if key or (seq and seq["still_picture"]):
            keys += 1
    # The IVF header's frame count is often 0 (unknown); report what is there.
    return {**head, "frames": len(frames), "first_pts": frames[0].pts if frames else None,
            "last_pts": frames[-1].pts if frames else None, "keyframes": keys,
            "obu_counts": dict(sorted(counts.items())), "sequence": seq}
