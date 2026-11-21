"""HEVC (H.265) Annex B streams: NAL units and the sequence parameter set (ITU-T H.265 7.3.2.2)."""
from fractions import Fraction

NAL_NAMES = {32: "VPS", 33: "SPS", 34: "PPS", 35: "AUD", 36: "EOS", 37: "EOB", 38: "FD", 39: "SEI_PREFIX", 40: "SEI_SUFFIX"}
SAR = {1: (1, 1), 2: (12, 11), 3: (10, 11), 4: (16, 11), 5: (40, 33), 6: (24, 11), 7: (20, 11), 8: (32, 11),
       9: (80, 33), 10: (18, 11), 11: (15, 11), 12: (64, 33), 13: (160, 99), 14: (4, 3), 15: (3, 2), 16: (2, 1)}
CHROMA = {0: "4:0:0", 1: "4:2:0", 2: "4:2:2", 3: "4:4:4"}


class HevcError(ValueError):
    pass


def nal_units(data):
    """Split an Annex B byte stream at 0x000001 start codes."""
    out, i, n = [], 0, len(data)
    starts = []
    while True:
        j = data.find(b"\0\0\1", i)
        if j < 0:
            break
        starts.append(j + 3)
        i = j + 3
    for k, s in enumerate(starts):
        e = starts[k + 1] - 3 if k + 1 < len(starts) else n
        while e > s and data[e - 1] == 0:
            e -= 1
        out.append(data[s:e])
    return out


def rbsp(nal):
    """Drop emulation prevention bytes (0x000003 -> 0x0000)."""
    out, zeros = bytearray(), 0
    for b in nal:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


class Bits:
    def __init__(self, data):
        self.d, self.p = data, 0

    def u(self, n):
        v = 0
        for _ in range(n):
            if self.p >> 3 >= len(self.d):
                raise HevcError("SPS ends early")
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def ue(self):
        zeros = 0
        while not self.u(1):
            zeros += 1
            if zeros > 31:
                raise HevcError("bad Exp-Golomb code")
        return (1 << zeros) - 1 + self.u(zeros)

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def _profile_tier_level(b, max_sub):
    b.u(2)
    tier = b.u(1)
    profile = b.u(5)
    b.u(32)
    b.u(4)
    b.u(43)
    b.u(1)
    level = b.u(8)
    return profile, tier, level


def _scaling_list_data(b):
    for size in range(4):
        for _ in range(6):
            if not b.u(1):
                b.ue()
            else:
                for _ in range(min(64, 1 << (4 + (size << 1)))):
                    b.se()


def _st_ref_pic_sets(b, count):
    sizes = []
    for idx in range(count):
        inter = b.u(1) if idx else 0
        if inter:
            b.u(1)
            b.ue()
            kept = 0
            for _ in range(sizes[0] + 1):
                used = b.u(1)
                use_delta = 1 if used else b.u(1)
                kept += use_delta
            sizes.append(kept)
        else:
            neg, pos = b.ue(), b.ue()
            for _ in range(neg + pos):
                b.ue()
                b.u(1)
            sizes.append(neg + pos)
    return sizes


def _vui(b, out):
    if b.u(1):
        idc = b.u(8)
        sar = SAR.get(idc)
        out["sar"] = "%d:%d" % sar if sar else None
    if b.u(1):
        b.u(1)
    if b.u(1):
        b.u(3)
        out["full_range"] = bool(b.u(1))
        if b.u(1):
            out["colour_primaries"], out["transfer_characteristics"], out["matrix_coeffs"] = b.u(8), b.u(8), b.u(8)
    if b.u(1):
        b.ue(); b.ue()
    b.u(1); b.u(1); b.u(1)
    b.u(1)                                           # default_display_window_flag
    if b.u(1):
        units, scale = b.u(32), b.u(32)
        out["frame_rate"] = str(Fraction(scale, 2 * units)) if units else None


def sps(payload):
    b = Bits(rbsp(payload)[2:])
    b.u(4)
    max_sub = b.u(3)
    b.u(1)
    profile, tier, level = _profile_tier_level(b, max_sub)
    out = {"id": b.ue(), "profile_idc": profile, "tier": "high" if tier else "main",
           "level": str(level), "max_sub_layers": max_sub + 1}
    chroma = b.ue()
    separate = 0
    width, height = b.ue(), b.ue()
    crop = [b.ue() for _ in range(4)] if b.u(1) else [0, 0, 0, 0]
    sub_w = sub_h = 1
    out.update(chroma_format=CHROMA[chroma], separate_colour_planes=bool(separate),
               width=width - sub_w * (crop[0] + crop[1]), height=height - sub_h * (crop[2] + crop[3]),
               coded_width=width, coded_height=height,
               bit_depth_luma=b.ue() + 8, bit_depth_chroma=b.ue() + 8)
    poc_bits = b.ue() + 4
    ordering = b.u(1)
    dpb = 0
    dpb = b.ue() + 1
    b.ue(); b.ue()
    out["max_dec_pic_buffering"] = dpb
    for _ in range(6):
        b.ue()
    out["scaling_lists"] = bool(b.u(1))
    if out["scaling_lists"] and b.u(1):
        _scaling_list_data(b)
    b.u(1)
    out["sao"] = bool(b.u(1))
    if b.u(1):
        b.u(4); b.u(4); b.ue(); b.ue(); b.u(1)
    out["short_term_ref_pic_sets"] = _st_ref_pic_sets(b, b.ue())
    lt = 0
    if b.u(1):
        lt = b.ue()
        for _ in range(lt):
            b.u(16)
            b.u(1)
    out["long_term_ref_pics"] = lt
    out["temporal_mvp"] = bool(b.u(1))
    b.u(1)
    out.update(sar=None, full_range=None, colour_primaries=None, transfer_characteristics=None,
               matrix_coeffs=None, frame_rate=None)
    if b.u(1):
        _vui(b, out)
    return out


def scan(data):
    counts, seqs, pictures = {}, {}, 0
    for nal in nal_units(data):
        if len(nal) < 2:
            continue
        if nal[0] & 0x80:
            raise HevcError("forbidden_zero_bit set")
        ntype = (nal[0] >> 1) & 0x3F
        layer = ((nal[0] & 1) << 5) | (nal[1] >> 3)
        name = NAL_NAMES.get(ntype, "VCL" if ntype < 32 else "other")
        counts[name] = counts.get(name, 0) + 1
        if layer:
            continue
        if ntype == 33:
            s = sps(nal)
            seqs[s["id"]] = s
        elif ntype < 32 and len(nal) > 2 and nal[2] & 0x80:
            pictures += 1
    return {"nal_units": dict(sorted(counts.items())), "pictures": pictures,
            "sps": [seqs[k] for k in sorted(seqs)]}
