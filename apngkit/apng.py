"""Animated PNG (APNG): chunks, acTL, fcTL timing and disposal, fdAT sequence, chunk CRCs."""
import struct
import zlib
from fractions import Fraction

SIG = b"\x89PNG\r\n\x1a\n"

# fcTL dispose_op values, in the order defined by the APNG specification:
# 0 = APNG_DISPOSE_OP_NONE, 1 = APNG_DISPOSE_OP_BACKGROUND, 2 = APNG_DISPOSE_OP_PREVIOUS.
DISPOSE = ("none", "background", "previous")
# fcTL blend_op values: 0 = APNG_BLEND_OP_SOURCE, 1 = APNG_BLEND_OP_OVER.
BLEND = ("source", "over")

# delay_den == 0 means the delay is given in 1/100 s (the spec's default of 100).
DEFAULT_DELAY_DEN = 100


class ApngError(ValueError):
    pass


def chunks(data):
    """Split a PNG stream into (type, body, crc_ok) tuples."""
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
        # The CRC covers the chunk type and the chunk data, not the length.
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
    if len(found[0][1]) < 8:
        raise ApngError("IHDR is too short")
    width, height = struct.unpack_from(">II", found[0][1])
    out = {"width": width, "height": height, "animated": False, "declared_frames": None, "frames": 0, "plays": None,
           "default_image_in_animation": None, "duration": None, "delays": [], "dispose": [], "blend": [],
           "sequence_ok": None, "regions_ok": None, "crc_errors": sum(1 for _, _, ok in found if not ok)}
    actl = None
    seen_idat = False
    default_image_in_animation = False
    frames = []       # (delay, dispose_op, blend_op), one per fcTL, in file order
    seqs = []         # fcTL and fdAT sequence numbers share one counter
    regions_ok = True
    for ctype, body, _ in found:
        if ctype == "acTL":
            if len(body) < 8:
                raise ApngError("acTL is too short")
            actl = struct.unpack_from(">II", body)
        elif ctype == "IDAT":
            # The default image is the first animation frame exactly when an
            # fcTL precedes the first IDAT chunk.
            if not seen_idat:
                default_image_in_animation = bool(frames)
            seen_idat = True
        elif ctype == "fcTL":
            if len(body) < 26:
                raise ApngError("fcTL is too short")
            seq, w, h, x, y, num, den, dop, bop = struct.unpack_from(">IIIIIHHBB", body)
            seqs.append(seq)
            # A delay_den of zero means the delay is expressed in 1/100 s.
            frames.append((Fraction(num, den or DEFAULT_DELAY_DEN), dop, bop))
            # Every frame region must be non-empty and fit inside the canvas.
            if w == 0 or h == 0 or x + w > width or y + h > height:
                regions_ok = False
        elif ctype == "fdAT":
            if len(body) < 4:
                raise ApngError("fdAT is too short")
            seqs.append(struct.unpack_from(">I", body)[0])
    if actl is None:
        return out
    declared_frames, num_plays = actl
    out.update(
        animated=True,
        declared_frames=declared_frames,
        # The frame count is the number of fcTL chunks; each frame has exactly
        # one, whether its data lives in the IDAT or in fdAT chunks.
        frames=len(frames),
        # num_plays == 0 means the animation loops forever.
        plays=None if num_plays == 0 else num_plays,
        default_image_in_animation=default_image_in_animation,
        # fcTL and fdAT sequence numbers must run 0, 1, 2, ... without gaps.
        sequence_ok=seqs == list(range(len(seqs))),
        regions_ok=regions_ok,
    )
    for delay, dop, bop in frames:
        out["delays"].append(str(delay))
        out["dispose"].append(DISPOSE[dop] if dop < len(DISPOSE) else "type_%d" % dop)
        out["blend"].append(BLEND[bop] if bop < len(BLEND) else "type_%d" % bop)
    # One loop is the sum of the per-frame delays; frames may differ in length.
    out["duration"] = str(sum((delay for delay, _, _ in frames), Fraction(0)))
    return out
