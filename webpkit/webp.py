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
        p += 8 + size + (size & 1)  # chunk payloads are padded to even sizes


def alpha_chunk(data, b, e):
    """Description of an ALPH chunk's header byte."""
    flags = data[b]
    method = flags & 0x03
    compression = ("none", "lossless")[method] if method < 2 else "type_%d" % method
    filtering = ("none", "horizontal", "vertical", "gradient")[(flags >> 2) & 0x03]
    return {"compression": compression, "filter": filtering, "preprocessing": (flags >> 4) & 0x03}


def bitstream(data, b, e, kind):
    """(width, height, alpha, entry) of a VP8 or VP8L bitstream."""
    if kind == b"VP8L":
        if data[b] != 0x2F:
            raise WebpError("bad VP8L signature")
        bits = struct.unpack_from("<I", data, b + 1)[0]
        entry = {"codec": "lossless", "version": (bits >> 29) & 0x07}
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool(bits & 0x10000000), entry
    tag = u24(data, b)
    if data[b + 3:b + 6] != b"\x9d\x01\x2a":
        raise WebpError("bad VP8 start code")
    w, h = struct.unpack_from("<HH", data, b + 6)
    entry = {"codec": "lossy", "key_frame": not tag & 1, "version": (tag >> 1) & 0x07,
             "show_frame": bool(tag & 0x10), "partition_size": tag >> 5,
             "scale": [(w >> 14) & 0x03, (h >> 14) & 0x03]}
    return w & 0x3FFF, h & 0x3FFF, False, entry


def frame_image(data, b, e):
    """Image data of one animation frame: codec, alpha and bitstream entry."""
    alph = None
    for cid, cb, ce in chunks(data, b, e):
        if cid == b"ALPH":
            alph = alpha_chunk(data, cb, ce)
        elif cid in (b"VP8 ", b"VP8L"):
            w, h, a, entry = bitstream(data, cb, ce, cid)
            entry["alpha_chunk"] = alph
            return {"codec": entry["codec"], "alpha": alph is not None or a, "bitstream": entry}
    return None


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise WebpError("not a WebP file")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])
    out = {"canvas": None, "animated": False, "alpha": False, "icc": False, "exif": False, "xmp": False,
           "background": None, "background_alpha": None, "loop_count": None, "frames": [], "duration": 0,
           "image": None, "bitstreams": []}
    alph = None  # ALPH chunk waiting for the still image's bitstream
    for cid, b, e in chunks(data, 12, end):
        if cid == b"VP8X":
            flags = data[b]
            out.update(icc=bool(flags & 0x20), alpha=bool(flags & 0x10), exif=bool(flags & 0x08),
                       xmp=bool(flags & 0x04), animated=bool(flags & 0x02),
                       canvas={"width": u24(data, b + 4) + 1, "height": u24(data, b + 7) + 1})
        elif cid == b"ANIM":
            bl, g, r, a = data[b:b + 4]  # background colour is stored B, G, R, A
            out["background"] = "#%02x%02x%02x" % (r, g, bl)
            out["background_alpha"] = a
            out["loop_count"] = struct.unpack_from("<H", data, b + 4)[0]
        elif cid == b"ANMF":
            x, y = u24(data, b) * 2, u24(data, b + 3) * 2  # offsets are stored halved
            w, h = u24(data, b + 6) + 1, u24(data, b + 9) + 1
            dur, flags = u24(data, b + 12), data[b + 15]
            img = frame_image(data, b + 16, e) or {"codec": None, "alpha": False, "bitstream": None}
            if img["bitstream"] is not None:
                out["bitstreams"].append(img["bitstream"])
            out["frames"].append({"x": x, "y": y, "width": w, "height": h, "duration": dur,
                                  "blend": bool(flags & 0x02), "dispose": bool(flags & 0x01),
                                  "codec": img["codec"], "alpha": img["alpha"]})
            out["duration"] += dur
        elif cid == b"ALPH":
            alph = alpha_chunk(data, b, e)
        elif cid in (b"VP8 ", b"VP8L"):
            w, h, a, entry = bitstream(data, b, e, cid)
            entry["alpha_chunk"] = alph
            if out["image"] is None:
                out["image"] = {"codec": entry["codec"], "width": w, "height": h,
                                "alpha": alph is not None or a}
            alph = None
            out["bitstreams"].append(entry)
    return out
