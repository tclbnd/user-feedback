"""Windows animated cursors (.ani): RIFF ACON, anih header, icon frames."""
import struct
from fractions import Fraction


class AniError(ValueError):
    pass


def chunks(data, p, end):
    while p + 8 <= end:
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        yield cid, p + 8, min(p + 8 + size, end)
        p += 8 + size


def icon_frame(d):
    """The first image of an icon resource."""
    if len(d) < 22:
        return None
    _, kind, count = struct.unpack_from("<HHH", d, 0)
    w, h, colors, _, planes, bpp, size, off = struct.unpack_from("<BBBBHHII", d, 6)
    return {"type": "icon", "width": w, "height": h, "bit_count": bpp, "png": False, "images": count, "hotspot": None}


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise AniError("not an animated cursor")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])
    head = None
    frames = []
    for cid, b, e in chunks(data, 12, end):
        if cid == b"anih":
            head = struct.unpack_from("<9I", data, b)
        elif cid == b"LIST" and data[b:b + 4] == b"fram":
            for c2, b2, e2 in chunks(data, b + 4, e):
                if c2 == b"icon":
                    frames.append(icon_frame(data[b2:e2]))
    if head is None:
        raise AniError("no anih chunk")
    _, nframes, nsteps, w, h, bits, planes, disp, attrs = head
    steps = list(range(nsteps))
    return {"frames": nframes, "steps": len(steps), "sequence": steps, "default_rate": disp,
            "loop_seconds": str(Fraction(disp * len(steps), 1000)), "icon_frames": bool(attrs & 1),
            "title": None, "artist": None, "frame_images": frames}
