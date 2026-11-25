"""Windows animated cursors (.ani): RIFF ACON, anih header, icon frames."""
import struct
from fractions import Fraction


class AniError(ValueError):
    pass


PNG_SIG = b"\x89PNG\r\n\x1a\n"


def chunks(data, p, end):
    """Each (id, start, end) in a chunk list; odd sizes carry a pad byte."""
    while p + 8 <= end:
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        yield cid, p + 8, min(p + 8 + size, end)
        p += 8 + size + (size & 1)


def png_size(d):
    """Width and height from a PNG's IHDR, or None if this is not a PNG."""
    if len(d) < 24 or d[:8] != PNG_SIG or d[12:16] != b"IHDR":
        return None
    return struct.unpack_from(">II", d, 16)


def image_bits(d, off, fallback=0):
    """Colour depth from an image's BITMAPINFOHEADER or PNG IHDR."""
    if d[off:off + 8] == PNG_SIG:
        if len(d) >= off + 26:
            depth, color = d[off + 24], d[off + 25]
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color, 0)
            return depth * channels or fallback
        return fallback
    if len(d) >= off + 16:
        bits = struct.unpack_from("<H", d, off + 14)[0]
        if bits:
            return bits
    return fallback


def icon_frame(d):
    """The first image of an icon (type 1) or cursor (type 2) resource."""
    size = png_size(d)  # a frame stored as a bare PNG
    if size is not None:
        return {"type": "icon", "width": size[0], "height": size[1], "bit_count": image_bits(d, 0),
                "png": True, "images": 1, "hotspot": None}
    if len(d) < 22:
        return None
    _, kind, count = struct.unpack_from("<HHH", d, 0)
    if kind not in (1, 2):
        return None
    w, h, _, _, planes, bpp, _, off = struct.unpack_from("<BBBBHHII", d, 6)
    is_png = d[off:off + 8] == PNG_SIG
    # A zero width or height stands for 256 pixels; a PNG knows its own size.
    size = png_size(d[off:]) if is_png else None
    width, height = size if size else (w or 256, h or 256)
    return {"type": "cursor" if kind == 2 else "icon", "width": width, "height": height,
            "bit_count": image_bits(d, off, bpp if kind == 1 else 0),
            "png": is_png, "images": count,
            "hotspot": [planes, bpp] if kind == 2 else None}


def info_string(d):
    return d.split(b"\0", 1)[0].decode("latin-1")


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise AniError("not an animated cursor")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])
    head = None
    frames = []
    rates = []
    order = None
    tags = {}
    for cid, b, e in chunks(data, 12, end):
        if cid == b"anih":
            head = struct.unpack_from("<9I", data, b)
        elif cid == b"rate":
            rates = list(struct.unpack_from("<%dI" % ((e - b) // 4), data, b))
        elif cid == b"seq ":
            order = list(struct.unpack_from("<%dI" % ((e - b) // 4), data, b))
        elif cid == b"LIST" and data[b:b + 4] == b"INFO":
            for c2, b2, e2 in chunks(data, b + 4, e):
                if c2 in (b"INAM", b"IART"):
                    tags[c2] = info_string(data[b2:e2])
        elif cid == b"LIST" and data[b:b + 4] == b"fram":
            for c2, b2, e2 in chunks(data, b + 4, e):
                if c2 == b"icon":
                    frame = icon_frame(data[b2:e2])
                    if frame:
                        frames.append(frame)
    if head is None:
        raise AniError("no anih chunk")
    _, nframes, nsteps, w, h, bits, planes, disp, attrs = head
    if order:
        sequence = order[:nsteps] if nsteps else order
    else:
        sequence = list(range(nsteps or nframes or len(frames)))
    steps = len(sequence)
    # The display rate is in 1/60 s ticks; a rate chunk gives one per step.
    ticks = [rates[i] if i < len(rates) else disp for i in range(steps)]
    return {"frames": nframes, "steps": steps, "sequence": sequence, "default_rate": disp,
            "loop_seconds": str(Fraction(sum(ticks), 60)), "icon_frames": bool(attrs & 1),
            "title": tags.get(b"INAM"), "artist": tags.get(b"IART"), "frame_images": frames}
