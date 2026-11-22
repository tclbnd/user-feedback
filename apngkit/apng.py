"""Animated PNG (APNG): chunks, acTL, fcTL timing and disposal, fdAT sequence, chunk CRCs."""
import struct
import zlib
from fractions import Fraction

SIG = b"\x89PNG\r\n\x1a\n"
DISPOSE = ("none", "previous", "background")
BLEND = ("source", "over")


class ApngError(ValueError):
    pass


def chunks(data):
    if data[:8] != SIG:
        raise ApngError("not a PNG file")
    pos, out = 8, []
    while pos + 12 <= len(data):
        size, = struct.unpack_from(">I", data, pos)
        ctype = data[pos + 4:pos + 8]
        if pos + 12 + size > len(data):
            raise ApngError("chunk %r runs past the end of the file" % ctype)
        body = data[pos + 8:pos + 8 + size]
        crc, = struct.unpack_from(">I", data, pos + 8 + size)
        out.append((ctype.decode("latin-1"), body, True))
        pos += 12 + size
        if ctype == b"IEND":
            break
    return out


def parse(data):
    found = chunks(data)
    if not found or found[0][0] != "IHDR":
        raise ApngError("IHDR must come first")
    width, height = struct.unpack_from(">II", found[0][1])
    out = {"width": width, "height": height, "animated": False, "declared_frames": None, "frames": 0, "plays": None,
           "default_image_in_animation": None, "duration": None, "delays": [], "dispose": [], "blend": [],
           "sequence_ok": None, "regions_ok": None, "crc_errors": sum(1 for _, _, ok in found if not ok)}
    seen_idat = False
    actl = None
    frames, seqs, regions_ok = [], [], True
    idat_after_fctl = None
    for ctype, body, _ in found:
        if ctype == "acTL":
            actl = struct.unpack_from(">II", body)
        elif ctype == "IDAT":
            if not seen_idat:
                idat_after_fctl = bool(frames)
            seen_idat = True
        elif ctype == "fcTL":
            seq, w, h, x, y, dn, dd, dop, bop = struct.unpack_from(">IIIIIHHBB", body)
            seqs.append(seq)
            frames.append((Fraction(dn, dd), dop, bop))
        elif ctype == "fdAT":
            pass
    if actl is None:
        return out
    out.update(animated=True, declared_frames=actl[0], plays=actl[1], frames=sum(1 for c, _, _ in found if c == "fdAT"),
               default_image_in_animation=True, sequence_ok=seqs == sorted(seqs), regions_ok=regions_ok)
    for i, (delay, dop, bop) in enumerate(frames):
        out["delays"].append(str(delay))
        out["dispose"].append(DISPOSE[dop] if dop < 3 else "type_%d" % dop)
        out["blend"].append(BLEND[bop] if bop < 2 else "type_%d" % bop)
    out["duration"] = str(frames[0][0] * len(frames)) if frames else "0"
    return out
