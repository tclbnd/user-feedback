"""Animated PNG (APNG): chunks, acTL, fcTL timing and disposal, fdAT sequence, chunk CRCs."""
import struct
import zlib
from fractions import Fraction

SIG = b"\x89PNG\r\n\x1a\n"
# APNG spec: dispose_op 0 = none, 1 = background, 2 = previous
DISPOSE = ("none", "background", "previous")
# APNG spec: blend_op 0 = source, 1 = over
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
        crc_ok = (zlib.crc32(ctype + body) & 0xFFFFFFFF) == crc
        out.append((ctype.decode("latin-1"), body, crc_ok))
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
    # Per the spec, when the first fcTL precedes the first IDAT, that IDAT
    # carries the first animation frame, i.e. the default image is frame 1.
    default_image_in_animation = None
    actl = None
    frames, seqs = [], []
    regions_ok = True
    for ctype, body, _ in found:
        if ctype == "acTL":
            actl = struct.unpack_from(">II", body)
        elif ctype == "IDAT":
            if not seen_idat:
                default_image_in_animation = bool(frames)
            seen_idat = True
        elif ctype == "fcTL":
            if len(body) < 26:
                raise ApngError("fcTL chunk is too short")
            seq, fw, fh, fx, fy, dn, dd, dop, bop = struct.unpack_from(">IIIIIHHBB", body)
            seqs.append(seq)
            # Spec: a frame delay denominator of 0 is treated as 100.
            delay = Fraction(dn, dd if dd else 100)
            frames.append((delay, dop, bop))
            # Spec: the frame region must be non-empty and fully inside the canvas.
            if fw == 0 or fh == 0 or fx + fw > width or fy + fh > height:
                regions_ok = False
        elif ctype == "fdAT":
            if len(body) < 4:
                raise ApngError("fdAT chunk is too short")
            # fcTL and fdAT share one sequence number space.
            seqs.append(struct.unpack_from(">I", body)[0])
    if actl is None:
        return out
    # One frame per fcTL, regardless of whether its image data is IDAT or fdAT.
    frame_count = sum(1 for c, _, _ in found if c == "fcTL")
    # Spec: num_plays of 0 means loop forever.
    plays = actl[1] if actl[1] != 0 else None
    # Spec: the sequence numbers of fcTL and fdAT chunks are 0, 1, 2, ... in order.
    sequence_ok = seqs == list(range(len(seqs)))
    out.update(animated=True, declared_frames=actl[0], plays=plays, frames=frame_count,
               default_image_in_animation=default_image_in_animation,
               sequence_ok=sequence_ok, regions_ok=regions_ok)
    for delay, dop, bop in frames:
        out["delays"].append(str(delay))
        out["dispose"].append(DISPOSE[dop] if dop < len(DISPOSE) else "type_%d" % dop)
        out["blend"].append(BLEND[bop] if bop < len(BLEND) else "type_%d" % bop)
    # Total play time of one loop is the sum of the individual frame delays.
    out["duration"] = str(sum((f[0] for f in frames), Fraction(0))) if frames else "0"
    return out
