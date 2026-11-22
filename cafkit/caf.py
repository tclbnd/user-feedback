"""Apple Core Audio Format (.caf): chunks, audio description, packet table, info strings."""
import struct
from fractions import Fraction


class CafError(ValueError):
    pass


def chunks(data):
    if data[:4] != b"caff" or len(data) < 8:
        raise CafError("not a CAF file")
    pos, out = 8, []
    while pos + 12 <= len(data):
        ctype = data[pos:pos + 4].decode("latin-1")
        size, = struct.unpack_from(">q", data, pos + 4)
        body = pos + 12
        if size < 0 or body + size > len(data):
            raise CafError("chunk %s runs past the end of the file" % ctype)
        out.append((ctype, body, size))
        pos = body + size
    return out


def varint(data, pos):
    return data[pos], pos + 1


def parse(data):
    found = chunks(data)
    if not found or found[0][0] != "desc":
        raise CafError("the first chunk must be desc")
    _, b, _ = found[0]
    rate, fmt, flags, bpp, fpp, ch, bits = struct.unpack_from(">d4sIIIII", data, b)
    out = {"sample_rate": rate, "format": fmt.decode("latin-1").strip(), "channels": ch, "bits": bits,
           "bytes_per_packet": bpp, "frames_per_packet": fpp, "float": bool(flags & 1) if fmt == b"lpcm" else None,
           "little_endian": bool(flags & 2) if fmt == b"lpcm" else None,
           "chunks": [c for c, _, _ in found], "edit_count": None, "data_bytes": None,
           "packets": None, "frames": None, "priming": 0, "remainder": 0, "duration": None,
           "max_packet_bytes": bpp or None, "max_packet_frames": fpp or None,
           "info": {}, "channel_layout": None}
    pakt = None
    for ctype, body, size in found:
        if ctype == "data":
            out["data_bytes"] = size
        elif ctype == "pakt":
            npk, valid, prime, rem = struct.unpack_from(">qqii", data, body)
            p, sizes, frames = body + 24, [], []
            for _ in range(npk):
                if bpp == 0:
                    s, p = varint(data, p)
                    sizes.append(s)
            pakt = {"packets": npk, "valid": valid, "priming": prime, "remainder": rem, "sizes": sizes, "frames": frames}
        elif ctype == "info":
            n, = struct.unpack_from("<I", data, body)
            parts = data[body + 4:body + size].split(b"\0")
            for k in range(n):
                out["info"][parts[2 * k].decode("utf-8")] = parts[2 * k + 1].decode("utf-8")
        elif ctype == "chan":
            tag, bitmap = struct.unpack_from(">II", data, body)
            out["channel_layout"] = {"tag": tag, "bitmap": bitmap}
    if pakt is not None:
        out["packets"] = pakt["packets"]
        out["frames"] = pakt["packets"] * fpp
        if pakt["sizes"]:
            out["max_packet_bytes"] = max(pakt["sizes"])
        if pakt["frames"]:
            out["max_packet_frames"] = max(pakt["frames"])
    elif bpp and out["data_bytes"] is not None:
        out["packets"] = out["data_bytes"] // bpp
        out["frames"] = out["packets"] * fpp
    if out["frames"] is not None:
        out["duration"] = str(Fraction(out["frames"]) / Fraction(rate))
    return out
