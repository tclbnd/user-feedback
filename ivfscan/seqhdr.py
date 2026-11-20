"""The first fields of an AV1 sequence header OBU."""


class Bits:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def f(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v


def parse(payload):
    b = Bits(payload)
    profile = b.f(3)
    still = b.f(1)
    reduced = b.f(1)
    level = b.f(5) if reduced else None
    if not reduced:
        b.f(1)
        b.f(1)
        b.f(5)
        b.f(12)
        level = b.f(5)
    wbits, hbits = b.f(4) + 1, b.f(4) + 1
    return {"profile": profile, "still_picture": bool(still), "level": level,
            "max_width": b.f(wbits) + 1, "max_height": b.f(hbits) + 1}
