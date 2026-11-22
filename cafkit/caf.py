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
        if size == -1:
            # Unknown size: the chunk (only the data chunk may do this) runs
            # to the end of the file. Recorders that stop mid-take, e.g. on
            # power loss, leave their files in this state.
            out.append((ctype, body, len(data) - body))
            break
        if size < 0 or body + size > len(data):
            raise CafError("chunk %s runs past the end of the file" % ctype)
        out.append((ctype, body, size))
        pos = body + size
    return out


def varint(data, pos):
    # CAF variable-length integer: 7 bits per byte, most significant byte
    # first; the high bit of a byte means another byte follows.
    v = 0
    while True:
        b = data[pos]
        pos += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, pos


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
            if size < 4:
                raise CafError("data chunk too small for the edit count")
            out["edit_count"], = struct.unpack_from(">I", data, body)
            out["data_bytes"] = size - 4  # the edit count is not audio
        elif ctype == "pakt":
            npk, valid, prime, rem = struct.unpack_from(">qqii", data, body)
            p, end = body + 24, body + size
            sizes, frames = [], []
            for _ in range(npk):
                if bpp == 0:
                    s, p = varint(data, p)
                    sizes.append(s)
                if fpp == 0:
                    f, p = varint(data, p)
                    frames.append(f)
            if p > end:
                raise CafError("pakt chunk entries run past the end of the chunk")
            pakt = {"packets": npk, "valid": valid, "priming": prime, "remainder": rem,
                    "sizes": sizes, "frames": frames}
        elif ctype == "info":
            n, = struct.unpack_from(">I", data, body)
            p, end = body + 4, body + size
            strings = []
            while p < end and len(strings) < 2 * n:
                z = data.index(b"\0", p, end)
                strings.append(data[p:z].decode("utf-8"))
                p = z + 1
            for k in range(0, len(strings) - 1, 2):
                out["info"][strings[k]] = strings[k + 1]
        elif ctype == "chan":
            tag, bitmap = struct.unpack_from(">II", data, body)
            out["channel_layout"] = {"tag": tag, "bitmap": bitmap}
    if pakt is not None:
        out["packets"] = pakt["packets"]
        out["priming"] = pakt["priming"]
        out["remainder"] = pakt["remainder"]
        total = sum(pakt["frames"]) if pakt["frames"] else pakt["packets"] * fpp
        # Playable frames: the valid-frame count already excludes the priming
        # and remainder frames; -1 means the writer left it unknown.
        if pakt["valid"] >= 0:
            out["frames"] = pakt["valid"]
        else:
            out["frames"] = max(0, total - pakt["priming"] - pakt["remainder"])
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
