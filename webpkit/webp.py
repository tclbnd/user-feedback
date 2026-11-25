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
        chunk_end = p + 8 + size
        if chunk_end > end:
            raise WebpError("chunk %r runs past its parent" % cid)
        yield cid, p + 8, chunk_end
        # RIFF chunks with an odd payload size have one zero padding byte.
        # Also accept concatenated test chunks that omit that padding.
        p = chunk_end
        if size & 1 and p < end and data[p] == 0:
            p += 1


def bitstream(data, b, e, kind):
    """(width, height, alpha) of a VP8 or VP8L bitstream."""
    if kind == b"VP8L":
        if e - b < 5:
            raise WebpError("truncated VP8L bitstream header")
        if data[b] != 0x2F:
            raise WebpError("bad VP8L signature")
        bits = struct.unpack_from("<I", data, b + 1)[0]
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        alpha = bool((bits >> 28) & 0x01)
        return width, height, alpha

    if e - b < 10 or data[b + 3:b + 6] != b"\x9d\x01\x2a":
        raise WebpError("bad VP8 start code")
    width_word, height_word = struct.unpack_from("<HH", data, b + 6)
    return width_word & 0x3FFF, height_word & 0x3FFF, False


def alpha_chunk(data, b, e):
    """Decode an ALPH chunk's compression, filter and preprocessing fields."""
    flags = data[b] if e > b else 0
    compression = flags & 0x03
    filters = ("none", "horizontal", "vertical", "gradient")
    return {
        "compression": {0: "none", 1: "lossless"}.get(compression, "type_%d" % compression),
        "filter": filters[(flags >> 2) & 0x03],
        "preprocessing": (flags >> 4) & 0x03,
    }


def bitstream_entry(data, b, e, kind, alpha_info):
    """Build the documented detailed bitstream record."""
    width, height, alpha = bitstream(data, b, e, kind)
    entry = {"codec": "lossless" if kind == b"VP8L" else "lossy"}

    if kind == b"VP8L":
        bits = struct.unpack_from("<I", data, b + 1)[0]
        entry["version"] = (bits >> 29) & 0x07
    else:
        frame_tag = u24(data, b)
        width_word, height_word = struct.unpack_from("<HH", data, b + 6)
        entry.update({
            "key_frame": not bool(frame_tag & 0x01),
            "version": (frame_tag >> 1) & 0x07,
            "show_frame": bool(frame_tag & 0x10),
            "partition_size": frame_tag >> 5,
            "scale": [width_word >> 14, height_word >> 14],
        })

    entry["alpha_chunk"] = alpha_info
    return entry, width, height, alpha


def frame_image(data, b, e, bitstreams=None):
    alpha_info = None
    for cid, cb, ce in chunks(data, b, e):
        if cid == b"ALPH":
            alpha_info = alpha_chunk(data, cb, ce)
        elif cid in (b"VP8 ", b"VP8L"):
            entry, w, h, a = bitstream_entry(data, cb, ce, cid, alpha_info)
            if bitstreams is not None:
                bitstreams.append(entry)
            return {"codec": entry["codec"], "alpha": a or alpha_info is not None,
                    "width": w, "height": h}
    return None


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise WebpError("not a WebP file")
    end = min(len(data), 8 + struct.unpack_from("<I", data, 4)[0])
    out = {"canvas": None, "animated": False, "alpha": False, "icc": False, "exif": False, "xmp": False,
           "background": None, "background_alpha": None, "loop_count": None, "frames": [], "duration": 0,
           "image": None, "bitstreams": []}
    still_alpha_info = None
    for cid, b, e in chunks(data, 12, end):
        if cid == b"VP8X":
            flags = data[b]
            out.update(icc=bool(flags & 0x20), alpha=bool(flags & 0x10), exif=bool(flags & 0x08),
                       xmp=bool(flags & 0x04), animated=bool(flags & 0x02),
                       canvas={"width": u24(data, b + 4) + 1, "height": u24(data, b + 7) + 1})
        elif cid == b"ANIM":
            blue, green, red, alpha = data[b:b + 4]
            out["background"] = "#%02x%02x%02x" % (red, green, blue)
            out["background_alpha"] = alpha
            out["loop_count"] = struct.unpack_from("<H", data, b + 4)[0]
        elif cid == b"ANMF":
            x, y = 2 * u24(data, b), 2 * u24(data, b + 3)
            w, h = u24(data, b + 6) + 1, u24(data, b + 9) + 1
            dur, flags = u24(data, b + 12), data[b + 15]
            img = frame_image(data, b + 16, e, out["bitstreams"]) or {"codec": None, "alpha": False}
            out["frames"].append({"x": x, "y": y, "width": w, "height": h, "duration": dur,
                                  "blend": not bool(flags & 0x02), "dispose": bool(flags & 0x01),
                                  "codec": img["codec"], "alpha": img["alpha"]})
            out["duration"] += dur
        elif cid == b"ALPH":
            still_alpha_info = alpha_chunk(data, b, e)
        elif cid in (b"VP8 ", b"VP8L"):
            entry, w, h, alpha = bitstream_entry(data, b, e, cid, still_alpha_info)
            out["bitstreams"].append(entry)
            if out["image"] is None:
                out["image"] = {"codec": entry["codec"], "width": w, "height": h,
                                "alpha": alpha or still_alpha_info is not None}
            still_alpha_info = None
    return out
