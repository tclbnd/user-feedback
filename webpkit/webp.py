"""WebP container (RIFF): VP8X features, animation, frames, still images."""
import struct


class WebpError(ValueError):
    pass


def u24(b, p):
    return b[p] | b[p + 1] << 8 | b[p + 2] << 16


def need(data, b, e, n, what):
    if e - b < n:
        raise WebpError("%s chunk is too short" % what)


def chunks(data, start, end):
    """Yield (id, payload_start, payload_end) for each chunk in [start, end).

    RIFF pads an odd-sized payload with one extra byte; that pad belongs to
    neither the chunk nor the next one, so it has to be skipped.
    """
    p = start
    while p + 8 <= end:
        cid, size = data[p:p + 4], struct.unpack_from("<I", data, p + 4)[0]
        if p + 8 + size > end:
            raise WebpError("chunk %r runs past its parent" % cid)
        yield cid, p + 8, p + 8 + size
        p += 8 + size + (size & 1)


def bitstream(data, b, e, kind):
    """(width, height, alpha) of a VP8 or VP8L bitstream."""
    if kind == b"VP8L":
        if e - b < 5 or data[b] != 0x2F:
            raise WebpError("bad VP8L signature")
        bits = struct.unpack_from("<I", data, b + 1)[0]
        # 14 bits width - 1, 14 bits height - 1, 1 bit alpha_is_used, 3 bits version.
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, bool(bits & 0x10000000)
    if e - b < 10 or data[b + 3:b + 6] != b"\x9d\x01\x2a":
        raise WebpError("bad VP8 start code")
    w, h = struct.unpack_from("<HH", data, b + 6)
    # 14 bits of each are the size; the top 2 bits are the scaling hint.
    return w & 0x3FFF, h & 0x3FFF, False


def frame_image(data, b, e):
    """Codec and alpha of a frame's image data, or None when it has none."""
    alpha, codec = False, None
    for cid, cb, ce in chunks(data, b, e):
        if cid == b"ALPH":
            alpha = True
        elif cid in (b"VP8 ", b"VP8L") and codec is None:
            _, _, a = bitstream(data, cb, ce, cid)
            alpha = alpha or a
            codec = "lossless" if cid == b"VP8L" else "lossy"
    if codec is None and not alpha:
        return None
    return {"codec": codec, "alpha": alpha}


def parse(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise WebpError("not a WebP file")
    end = 8 + struct.unpack_from("<I", data, 4)[0]
    if end < 12 or end > len(data):
        # A bogus RIFF size must not hide the chunks that are actually there.
        end = len(data)

    out = {"canvas": None, "animated": False, "alpha": False, "icc": False, "exif": False, "xmp": False,
           "background": None, "background_alpha": None, "loop_count": None, "frames": [], "duration": 0, "image": None}
    still_alpha = False
    for cid, b, e in chunks(data, 12, end):
        if cid == b"VP8X":
            need(data, b, e, 10, "VP8X")
            flags = data[b]
            out.update(icc=bool(flags & 0x20), alpha=bool(flags & 0x10), exif=bool(flags & 0x08),
                       xmp=bool(flags & 0x04), animated=bool(flags & 0x02),
                       # Canvas size is stored as (actual width/height - 1).
                       canvas={"width": u24(data, b + 4) + 1, "height": u24(data, b + 7) + 1})
        elif cid == b"ANIM":
            need(data, b, e, 6, "ANIM")
            # Background colour is stored as BGRA.
            bl, g, r, a = data[b:b + 4]
            out["background"] = "#%02x%02x%02x" % (r, g, bl)
            out["background_alpha"] = a
            out["loop_count"] = struct.unpack_from("<H", data, b + 4)[0]
        elif cid == b"ANMF":
            need(data, b, e, 16, "ANMF")
            # Frame offsets are stored divided by two; frame size is stored minus one.
            x, y = u24(data, b) * 2, u24(data, b + 3) * 2
            w, h = u24(data, b + 6) + 1, u24(data, b + 9) + 1
            dur, flags = u24(data, b + 12), data[b + 15]
            img = frame_image(data, b + 16, e) or {"codec": None, "alpha": False}
            out["frames"].append({"x": x, "y": y, "width": w, "height": h, "duration": dur,
                                  # Bit 1: blending method, set means "do not blend".
                                  # Bit 0: disposal method, set means "dispose to background".
                                  "blend": not flags & 0x02, "dispose": bool(flags & 0x01),
                                  "codec": img["codec"], "alpha": img["alpha"]})
            out["duration"] += dur
        elif cid == b"ALPH":
            still_alpha = True
        elif cid in (b"VP8 ", b"VP8L") and out["image"] is None:
            # A still image's size comes from its own bitstream header.
            w, h, a = bitstream(data, b, e, cid)
            out["image"] = {"codec": "lossless" if cid == b"VP8L" else "lossy", "width": w, "height": h, "alpha": a}
    if out["image"] is not None and still_alpha:
        out["image"]["alpha"] = True
    if out["animated"]:
        # An animation's pixels live in its frames, never in a top-level bitstream.
        out["image"] = None
    return out
