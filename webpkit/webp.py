"""WebP container (RIFF): VP8X features, animation, frames, still images."""
import struct


class WebpError(ValueError):
    pass


def u24(b, p):
    return b[p] | b[p + 1] << 8 | b[p + 2] << 16


def chunks(data, start, end):
    p = start
    while p + 8 <= end:
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        if p + 8 + size > end:
            raise WebpError("chunk %r runs past its parent" % cid)
        yield cid, p + 8, p + 8 + size
        p += 8 + size


def bitstream(data, b, e, kind):
    """(width, height, alpha) of a VP8 or VP8L bitstream."""
    if kind == b"VP8L":
        if data[b] != 0x2F:
            raise WebpError("bad VP8L signature")
        bits = struct.unpack_from("<I", data, b + 1)[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, False
    if data[b + 3:b + 6] != b"\x9d\x01\x2a":
        raise WebpError("bad VP8 start code")
    w, h = struct.unpack_from("<HH", data, b + 6)
    return w, h, False


def frame_image(data, b, e):
    alpha = False
    for cid, cb, ce in chunks(data, b, e):
        if cid == b"ALPH":
            alpha = True
        elif cid in (b"VP8 ", b"VP8L"):
            w, h, a = bitstream(data, cb, ce, cid)
            return {"codec": "lossless" if cid == b"VP8L" else "lossy", "alpha": alpha or a, "width": w, "height": h}
    return None


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise WebpError("not a WebP file")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])
    out = {"canvas": None, "animated": False, "alpha": False, "icc": False, "exif": False, "xmp": False,
           "background": None, "background_alpha": None, "loop_count": None, "frames": [], "duration": 0, "image": None}
    for cid, b, e in chunks(data, 12, end):
        if cid == b"VP8X":
            flags = data[b]
            out.update(icc=bool(flags & 0x20), alpha=bool(flags & 0x10), exif=bool(flags & 0x08),
                       xmp=bool(flags & 0x04), animated=bool(flags & 0x02),
                       canvas={"width": u24(data, b + 4), "height": u24(data, b + 7)})
        elif cid == b"ANIM":
            r, g, bl, a = data[b:b + 4]
            out["background"] = "#%02x%02x%02x" % (r, g, bl)
            out["background_alpha"] = a
            out["loop_count"] = struct.unpack_from("<H", data, b + 4)[0]
        elif cid == b"ANMF":
            x, y = u24(data, b), u24(data, b + 3)
            w, h = u24(data, b + 6) + 1, u24(data, b + 9) + 1
            dur, flags = u24(data, b + 12), data[b + 15]
            img = frame_image(data, b + 16, e) or {"codec": None, "alpha": False}
            out["frames"].append({"x": x, "y": y, "width": w, "height": h, "duration": dur,
                                  "blend": not flags & 0x01, "dispose": bool(flags & 0x02),
                                  "codec": img["codec"], "alpha": img["alpha"]})
            out["duration"] += dur
        elif cid in (b"VP8 ", b"VP8L") and out["image"] is None:
            w, h, a = bitstream(data, b, e, cid)
            out["image"] = {"codec": "lossless" if cid == b"VP8L" else "lossy", "width": w, "height": h, "alpha": a}
    return out
