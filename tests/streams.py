"""Builders for small synthetic HEVC streams used by the tests."""


class W:
    def __init__(self):
        self.bits = []

    def u(self, n, v):
        self.bits += [(v >> (n - 1 - i)) & 1 for i in range(n)]
        return self

    def ue(self, v):
        v += 1
        n = v.bit_length()
        return self.u(n - 1, 0).u(n, v)

    def se(self, v):
        return self.ue(2 * v - 1 if v > 0 else -2 * v)

    def data(self):
        bits = self.bits + [1]
        bits += [0] * (-len(bits) % 8)
        return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def ep(raw):
    out, zeros = bytearray(), 0
    for b in raw:
        if zeros >= 2 and b <= 3:
            out.append(3)
            zeros = 0
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


def nal(ntype, payload, layer=0, tid=1):
    return bytes([ntype << 1 | layer >> 5, (layer & 31) << 3 | tid]) + ep(payload)


def ptl(w, profile=1, tier=0, level=120, sub=0, sub_present=None):
    w.u(2, 0).u(1, tier).u(5, profile).u(32, 0x60000000).u(4, 0b1001).u(43, 0).u(1, 0).u(8, level)
    sub_present = sub_present or [(0, 0)] * sub
    for p, l in sub_present:
        w.u(1, p).u(1, l)
    if sub:
        for _ in range(sub, 8):
            w.u(2, 0)
    for p, l in sub_present:
        if p:
            w.u(32, 0x12345678).u(32, 0x9ABCDEF0).u(24, 0x55AA55)
        if l:
            w.u(8, 90)


def sps(width=1920, height=1088, crop=None, depth=(8, 8), profile=1, tier=0, level=120, sid=0, sao=1, tmvp=1, rate=None):
    chroma, separate, sub, sub_present, ordering, dpb, poc_bits = 1, 0, 0, None, 1, None, 8
    scaling, pcm, rps, lt, zeros = None, False, None, None, 0
    vui = {"rate": rate} if rate else None
    w = W().u(4, 0).u(3, sub).u(1, 1)
    ptl(w, profile, tier, level, sub, sub_present)
    w.ue(sid).ue(chroma)
    if chroma == 3:
        w.u(1, separate)
    w.ue(width).ue(height)
    if crop is None:
        crop = (0, 0, 0, 4) if height == 1088 else (0, 0, 0, 0)
    if any(crop):
        w.u(1, 1)
        for c in crop:
            w.ue(c)
    else:
        w.u(1, 0)
    w.ue(depth[0] - 8).ue(depth[1] - 8).ue(poc_bits - 4)
    dpb = dpb or [5] * (sub + 1)
    w.u(1, ordering)
    for d in (dpb if ordering else dpb[-1:]):
        w.ue(d - 1).ue(2).ue(0)
    for v in (0, 3, 0, 3, 1 + zeros, 1):
        w.ue(v)
    w.u(1, 1 if scaling else 0)
    if scaling:
        w.u(1, 1 if scaling == "data" else 0)
        if scaling == "data":
            for size in range(4):
                for m in range(0, 6, 3 if size == 3 else 1):
                    if m == 0 or size == 3:
                        w.u(1, 1)
                        if size > 1:
                            w.se(8)
                        for k in range(min(64, 1 << (4 + (size << 1)))):
                            w.se(1 if k % 7 == 0 else -1 if k % 7 == 3 else 0)
                    else:
                        w.u(1, 0).ue(m % 2)
    w.u(1, 1).u(1, sao).u(1, 1 if pcm else 0)
    if pcm:
        w.u(4, 7).u(4, 7).ue(0).ue(1).u(1, 1)
    rps = rps if rps is not None else [("n", [(1, 1), (1, 1)], [])]
    w.ue(len(rps))
    for i, r in enumerate(rps):
        if i:
            w.u(1, 1 if r[0] == "i" else 0)
        if r[0] == "i":
            w.u(1, r[1]).ue(r[2] - 1)
            for used, use_delta in r[3]:
                w.u(1, used)
                if not used:
                    w.u(1, use_delta)
        else:
            w.ue(len(r[1])).ue(len(r[2]))
            for d, used in r[1] + r[2]:
                w.ue(d - 1).u(1, used)
    w.u(1, 1 if lt else 0)
    if lt:
        w.ue(len(lt))
        for poc, used in lt:
            w.u(poc_bits, poc).u(1, used)
    w.u(1, tmvp).u(1, 1)
    w.u(1, 1 if vui else 0)
    if vui:
        sar = vui.get("sar")
        w.u(1, 1 if sar else 0)
        if sar:
            if isinstance(sar, int):
                w.u(8, sar)
            else:
                w.u(8, 255).u(16, sar[0]).u(16, sar[1])
        w.u(1, 0)
        col = vui.get("colour")
        w.u(1, 1 if col else 0)
        if col:
            w.u(3, 5).u(1, col[0]).u(1, 1).u(8, col[1]).u(8, col[2]).u(8, col[3])
        w.u(1, 1 if vui.get("chroma_loc") else 0)
        if vui.get("chroma_loc"):
            w.ue(vui["chroma_loc"]).ue(vui["chroma_loc"])
        w.u(1, 0).u(1, 0).u(1, 0)
        win = vui.get("window")
        w.u(1, 1 if win else 0)
        if win:
            for v in win:
                w.ue(v)
        fr = vui.get("rate")
        w.u(1, 1 if fr else 0)
        if fr:
            w.u(32, fr[0]).u(32, fr[1]).u(1, 0).u(1, 0)
        w.u(1, 0)
    w.u(1, 0)
    return nal(33, w.data())


def vps():
    return nal(32, bytes([0x0C, 0x01, 0xFF, 0xFF, 0x01, 0x60, 0, 0, 0, 0x90, 0, 0, 0, 0, 0, 0x78, 0x95, 0x98, 0x09]))


def pps():
    return nal(34, bytes([0xC1, 0x72, 0xB4, 0x62, 0x40]))


def slice_(irap=True, first=True, ntype=None, layer=0):
    t = ntype if ntype is not None else (19 if irap else 1)
    return nal(t, bytes([0x80 if first else 0x00, 0xA4, 0x12, 0x34]), layer=layer)


def stream(*nals, long_codes=True):
    out = b""
    for i, n in enumerate(nals):
        out += (b"\0\0\0\1" if long_codes or i == 0 else b"\0\0\1") + n
    return out


def clip(seq, pictures=10, slices=1, aud=False):
    out = [vps(), seq, pps()]
    for p in range(pictures):
        if aud:
            out.append(nal(35, b"\x50"))
        for s in range(slices):
            out.append(slice_(p == 0, s == 0))
    return stream(*out)
