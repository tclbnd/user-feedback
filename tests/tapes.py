"""Builders for small synthetic DV captures used by the tests."""


def bcd(v):
    return (v // 10) << 4 | v % 10


def tc_pack(h, m, s, f, drop=False):
    return bytes([0x13, bcd(f) | (0x40 if drop else 0), bcd(s), bcd(m), bcd(h)])


def as_pack(rate=48000, af=20, locked=True, twelve=False, pal=False):
    stereo = 0
    smp = {48000: 0, 44100: 1, 32000: 2}[rate]
    return bytes([0x50, (0 if locked else 0x80) | af, stereo, 0x20 if pal else 0, smp << 3 | (1 if twelve else 0)])


def vsc_pack(disp=0):
    return bytes([0x61, 0x3F, 0xC8 | disp, 0xFC, 0xFF])


def date_pack(y, m, d):
    return bytes([0x62, 0xFF, bcd(d), bcd(m), bcd(y % 100)])


def time_pack(h, m, s):
    return bytes([0x63, 0xFF, bcd(s), bcd(m), bcd(h)])


def block(sct, seq, num, body, fsc=0):
    head = bytes([sct << 5 | 0x07, seq << 4 | (0x08 if fsc else 0) | 0x07, num])
    return (head + body).ljust(80, b"\xff")


def frame(pal=False, tc=(0, 0, 0, 0), audio=None, disp=0, date=None, time=None, drop=False, channels=1):
    af = None
    seqs = 12 if pal else 10
    out = b""
    for ch in range(channels):
        for s in range(seqs):
            out += block(0, s, 0, bytes([0x80 if pal else 0, 0x68, 0x78, 0x78, 0x78]), ch)
            ss = b""
            for k in range(6):
                ss += bytes([0x80, k, 0xFF]) + (tc_pack(*tc, drop=drop) if k in (2, 5) else b"\xff" * 5)
            out += block(1, s, 0, ss, ch) + block(1, s, 1, ss, ch)
            vaux = [b"\xff" * 5] * 45
            if s == 0:
                vaux[39] = bytes([0x60, 0xFF, 0xFF, 0x20 if pal else 0, 0xFF])
                vaux[40] = vsc_pack(disp)
                if date:
                    vaux[41] = date_pack(*date)
                if time:
                    vaux[42] = time_pack(*time)
            for v in range(3):
                out += block(2, s, v, b"".join(vaux[v * 15:v * 15 + 15]), ch)
            for a in range(9):
                pack = b"\xff" * 5
                slot = 3 if s % 2 == 0 else 0
                if audio and a == slot:
                    n = audio.get("af", 20 if af is None else af)
                    pack = as_pack(audio.get("rate", 48000), n, audio.get("locked", True), audio.get("twelve", False), pal)
                out += block(3, s, a, pack + b"\0" * 72, ch)
                for v in range(15):
                    out += block(4, s, a * 15 + v, b"\0" * 77, ch)
    return out


def tc_add(tc, n, fps):
    h, m, s, f = tc
    total = ((h * 60 + m) * 60 + s) * fps + f + n
    return (total // (fps * 3600), total // (fps * 60) % 60, total // fps % 60, total % fps)


def clip(n=10, pal=False, start=(10, 0, 0, 0), audio={"rate": 48000}, **kw):
    fps = 25 if pal else 30
    return b"".join(frame(pal, tc_add(start, i, fps), audio=audio, **kw) for i in range(n))
