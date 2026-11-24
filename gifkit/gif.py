"""Animated GIF (87a / 89a): screen, colour tables, extensions, frames, timing, looping."""
import struct
from fractions import Fraction

DISPOSAL = {0: "none", 1: "keep", 2: "background", 3: "previous"}


class GifError(ValueError):
    pass


def sub_blocks(data, p):
    """Concatenate data sub-blocks starting at p; return (payload, position after the terminator)."""
    out = bytearray()
    while True:
        if p >= len(data):
            raise GifError("data sub-blocks run past the end of the file")
        n = data[p]
        p += 1
        if n == 0:
            return bytes(out), p
        out += data[p:p + n]
        p += n


def parse(data):
    if data[:3] != b"GIF" or data[3:6] not in (b"87a", b"89a"):
        raise GifError("not a GIF file")
    w, h, packed, bg, aspect = struct.unpack_from("<HHBBB", data, 6)
    out = {"version": data[3:6].decode(), "width": w, "height": h,
           "global_colors": 1 << (packed & 7) if packed & 0x80 else None, "background": bg,
           "aspect": "%.3f" % (aspect / 64) if aspect else None,
           "loop_count": None, "frames": [], "duration": "0", "comments": [], "truncated": False}
    p = 13 + (3 * out["global_colors"] if out["global_colors"] else 0)
    gce = None
    total = Fraction(0)
    while True:
        if p >= len(data):
            out["truncated"] = True
            break
        tag = data[p]
        if tag == 0x3B:
            break
        if tag == 0x21:
            label = data[p + 1]
            if label == 0xF9:
                size, flags, delay, transp = struct.unpack_from("<BBHB", data, p + 2)
                gce = {"delay": delay, "disposal": DISPOSAL.get(flags & 7, "type_%d" % (flags & 7)),
                       "transparent": transp}
                _, p = sub_blocks(data, p + 2 + 1 + size)
                continue
            if label == 0xFF:
                ident = data[p + 3:p + 3 + data[p + 2]]
                payload, p = sub_blocks(data, p + 3 + data[p + 2])
                if ident == b"NETSCAPE2.0" and len(payload) >= 3:
                    out["loop_count"] = struct.unpack_from(">H", payload, 1)[0]
                continue
            if label == 0xFE:
                payload, p = sub_blocks(data, p + 2)
                out["comments"].append(payload.decode("latin-1"))
                continue
            _, p = sub_blocks(data, p + 2)
            continue
        if tag == 0x2C:
            x, y, fw, fh, flags = struct.unpack_from("<HHHHB", data, p + 1)
            p += 10
            local = 1 << (flags & 7) if flags & 0x80 else None
            if local:
                p += 3 * local
            p += 1
            p += 1 + data[p]
            if data[p] == 0:
                p += 1
            g = gce or {"delay": 0, "disposal": "none", "transparent": None}
            out["frames"].append({"x": x, "y": y, "width": fw, "height": fh, "interlaced": bool(flags & 0x40),
                                  "local_colors": local, **g})
            total += Fraction(g["delay"], 1000)
            continue
        raise GifError("unexpected block 0x%02X at %d" % (tag, p))
    out["duration"] = str(total)
    return out
