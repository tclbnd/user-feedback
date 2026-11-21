"""MSB-first bit reader/writer."""


class Reader:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def u(self, n):
        v = 0
        for _ in range(n):
            i = self.pos >> 3
            if i >= len(self.data):
                raise EOFError("read past end")
            v = (v << 1) | ((self.data[i] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v
