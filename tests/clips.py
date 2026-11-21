"""Builders for small synthetic AV1-in-IVF clips used by the tests."""
import struct


class W:
    def __init__(self):
        self.bits = ""

    def f(self, n, v):
        self.bits += format(v, "0%db" % n) if n else ""
        return self

    def uvlc(self, v):
        v += 1
        n = v.bit_length() - 1
        self.bits += "0" * n + format(v, "b")
        return self

    def data(self):
        b = self.bits + "1"                                 # trailing one bit
        b += "0" * (-len(b) % 8)
        return int(b, 2).to_bytes(len(b) // 8, "big")


def seq_header(width=1920, height=1080, profile=0, level=8, tier=0, timing=None, ticks=None):
    decoder_model, ops, initial_delay, reduced, still, wbits, hbits = False, None, False, False, False, None, None
    frame_id, sb128, order_hint_bits, sct, high, twelve, mono = None, False, 7, (1, 0), False, False, False
    color, color_range, sub12, csp, film_grain = None, False, (1, 1), 0, False
    w = W().f(3, profile).f(1, 1 if still or reduced else 0).f(1, 1 if reduced else 0)
    if reduced:
        w.f(5, level)
    else:
        w.f(1, 1 if timing else 0)
        if timing:
            w.f(32, timing[0]).f(32, timing[1]).f(1, 1 if ticks else 0)
            if ticks:
                w.uvlc(ticks - 1)
            w.f(1, 1 if decoder_model else 0)
            if decoder_model:
                w.f(5, 9).f(32, 1).f(5, 9).f(5, 9)
        w.f(1, 1 if initial_delay else 0)
        points = [(0, level, tier)] + list(ops or [])
        w.f(5, len(points) - 1)
        for idc, lv, tr in points:
            w.f(12, idc).f(5, lv)
            if lv > 7:
                w.f(1, tr)
            if decoder_model:
                w.f(1, 1).f(10, 300).f(10, 200).f(1, 0)
            if initial_delay:
                w.f(1, 1).f(4, 9)
    wb = wbits or max(width - 1, 1).bit_length()
    hb = hbits or max(height - 1, 1).bit_length()
    w.f(4, wb - 1).f(4, hb - 1).f(wb, width - 1).f(hb, height - 1)
    if not reduced:
        w.f(1, 1 if frame_id else 0)
        if frame_id:
            w.f(4, frame_id[0]).f(3, frame_id[1])
    w.f(1, 1 if sb128 else 0).f(1, 1).f(1, 1)
    if not reduced:
        w.f(1, 1).f(1, 1).f(1, 0).f(1, 1).f(1, 1 if order_hint_bits else 0)
        if order_hint_bits:
            w.f(1, 1).f(1, 1)
        choose, force = sct
        w.f(1, choose)
        if not choose:
            w.f(1, force)
        if choose or force:
            w.f(1, 1)                                        # seq_choose_integer_mv
        if order_hint_bits:
            w.f(3, order_hint_bits - 1)
    w.f(1, 0).f(1, 1).f(1, 1)
    w.f(1, 1 if high else 0)
    depth = 8
    if profile == 2 and high:
        w.f(1, 1 if twelve else 0)
        depth = 12 if twelve else 10
    elif high:
        depth = 10
    if profile != 1:
        w.f(1, 1 if mono else 0)
    w.f(1, 1 if color else 0)
    if color:
        w.f(8, color[0]).f(8, color[1]).f(8, color[2])
    cp, tc, mc = color or (2, 2, 2)
    if mono:
        w.f(1, 1 if color_range else 0)
    elif cp == 1 and tc == 13 and mc == 0:
        w.f(1, 0)
    else:
        w.f(1, 1 if color_range else 0)
        if profile == 0:
            sx, sy = 1, 1
        elif profile == 1:
            sx, sy = 0, 0
        elif depth == 12:
            sx, sy = sub12
            w.f(1, sx)
            if sx:
                w.f(1, sy)
        else:
            sx, sy = 1, 0
        if sx and sy:
            w.f(2, csp)
        w.f(1, 0)
    w.f(1, 1 if film_grain else 0)
    return w.data()


def leb128(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def obu(otype, payload=b"", ext=None):
    has_size = True
    h = (otype << 3) | (4 if ext is not None else 0) | (2 if has_size else 0)
    out = bytes([h])
    if ext is not None:
        out += bytes([(ext[0] << 5) | (ext[1] << 3)])
    return out + (leb128(len(payload)) if has_size else b"") + payload


def frame_obu(size=60, seed=0, ext=None):
    return obu(6, bytes(((i * 11 + seed) & 0xFF) for i in range(size)), ext)


def ivf(frames, width=1920, height=1080, rate=30, scale=1, fourcc=b"AV01"):
    """frames: list of (pts, bytes)."""
    head = b"DKIF" + struct.pack("<HH4sHHIII", 0, 32, fourcc, width, height, rate, scale, len(frames)) + bytes(4)
    return head + b"".join(struct.pack("<IQ", len(f), pts) + f for pts, f in frames)


def clip(n=10, seq=None, size=60, ext=None, td=True, step=1):
    seq = seq or seq_header()
    out = []
    for i in range(n):
        tu = (obu(2, ext=ext) if td else b"") + (obu(1, seq, ext) if i == 0 else b"") + frame_obu(size, i, ext)
        out.append((i * step, tu))
    return out
