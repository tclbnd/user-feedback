"""seq_parameter_set_rbsp() and the VUI fields the farm needs."""
from fractions import Fraction

from .bits import BitReader


class SpsError(ValueError):
    pass


def parse(nal):
    if not nal or nal[0] & 0x1F != 7:
        raise SpsError("not an SPS NAL unit")
    r = BitReader(nal[1:])
    profile = r.u(8)
    constraints = r.u(8)
    level = r.u(8)
    r.ue()
    chroma, depth_l, depth_c = 1, 8, 8
    if profile == 100:
        chroma = r.ue()
        depth_l = r.ue() + 8
        depth_c = r.ue() + 8
        r.flag()
        r.flag()
    r.ue()
    poc_type = r.ue()
    if poc_type == 0:
        r.ue()
    r.ue()
    r.flag()
    w_mbs = r.ue() + 1
    h_units = r.ue() + 1
    frame_mbs_only = r.flag()
    if not frame_mbs_only:
        r.flag()
    r.flag()
    width, height = w_mbs * 16, h_units * 16
    if r.flag():
        left, right, top, bottom = r.ue(), r.ue(), r.ue(), r.ue()
        width -= 2 * (left + right)
        height -= 2 * (top + bottom)
    out = {"profile_idc": profile, "constraint_flags": constraints, "level": str(level / 10).rstrip("0").rstrip("."),
           "chroma_format_idc": chroma, "bit_depth_luma": depth_l, "bit_depth_chroma": depth_c,
           "frame_mbs_only": frame_mbs_only, "width": width, "height": height,
           "sar": "1:1", "fps": None, "full_range": False}
    if r.flag():
        if r.flag():
            idc = r.u(8)
            out["sar"] = {1: "1:1", 2: "12:11", 3: "10:11", 4: "16:11", 14: "4:3"}.get(idc, "1:1")
        if r.flag():
            r.flag()
        if r.flag():
            r.u(3)
            out["full_range"] = r.flag()
            if r.flag():
                r.u(24)
        if r.flag():
            r.ue()
            r.ue()
        if r.flag():
            units, scale = r.u(32), r.u(32)
            out["fps"] = str(Fraction(scale, units))
    return out
