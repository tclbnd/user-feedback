"""Builders for small synthetic video elementary streams used by the tests."""
def _b(bits):
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big") if bits else b""


def _f(v, n):
    return format(v, "0%db" % n)


def seq(w, h, aspect, frc, br, vbv=112, intra=False):
    s = _f(w & 0xFFF, 12) + _f(h & 0xFFF, 12) + _f(aspect, 4) + _f(frc, 4) + _f(br, 18) + "1" + _f(vbv, 10) + "0"
    s += ("1" + "00010000" * 64) if intra else "0"
    s += "0"
    return b"\x00\x00\x01\xb3" + _b(s)


def seq_ext(profile=0x48, prog=1, chroma=1, hext=0, vext=0, brext=0, vbvext=0, n=0, d=0):
    s = "0001" + _f(profile, 8) + str(prog) + _f(chroma, 2) + _f(hext, 2) + _f(vext, 2) + _f(brext, 12) + "1" + _f(vbvext, 8) + "0" + _f(n, 2) + _f(d, 5)
    return b"\x00\x00\x01\xb5" + _b(s)


def disp_ext(dw, dh, colour=False):
    s = "0010" + "101" + ("1" + _f(1, 8) * 3 if colour else "0") + _f(dw, 14) + "1" + _f(dh, 14)
    return b"\x00\x00\x01\xb5" + _b(s)


def gop(closed=1):
    return b"\x00\x00\x01\xb8" + _b("0" + _f(1, 5) + _f(0, 6) + "1" + _f(0, 6) + _f(0, 6) + str(closed) + "0")


def pic(kind, tr=0, mpeg1=False):
    code = {"I": 1, "P": 2, "B": 3, "D": 4}[kind]
    s = _f(tr, 10) + _f(code, 3) + _f(0xFFFF, 16)
    if code in (2, 3):
        s += "0" + ("001" if mpeg1 else "111")
    if code == 3:
        s += "0" + ("001" if mpeg1 else "111")
    s += "0"
    return b"\x00\x00\x01\x00" + _b(s)


def pce(structure=3, tff=0, rff=0, prog=1):
    s = "1000" + "1111" * 4 + "00" + _f(structure, 2) + str(tff) + "1" + "0000" + str(rff) + str(prog) + str(prog) + "0"
    return b"\x00\x00\x01\xb5" + _b(s)


SLICE = b"\x00\x00\x01\x01\x0a\x55\xaa\x13"
END = b"\x00\x00\x01\xb7"


def mpeg2(header, frames, gop_every=0):
    """frames: list of (type, pce kwargs) for frame pictures, or lists of two for field pairs."""
    out = bytearray(header)
    for i, item in enumerate(frames):
        if gop_every and i % gop_every == 0 or i == 0:
            out += gop()
        for kind, kw in (item if isinstance(item, list) else [item]):
            out += pic(kind) + pce(**kw) + SLICE
    return bytes(out + END)


def mpeg1(header, kinds):
    out = bytearray(header) + gop()
    for k in kinds:
        out += pic(k, mpeg1=True) + SLICE
    return bytes(out + END)
