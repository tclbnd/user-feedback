"""MSB-first bit reader."""


class Reader:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos * 8

    def u(self, n):
        v = 0
        for _ in range(n):
            i = self.pos >> 3
            if i >= len(self.data):
                raise EOFError("read past end of section")
            v = (v << 1) | ((self.data[i] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def take(self, n):
        assert self.pos % 8 == 0
        i = self.pos >> 3
        if i + n > len(self.data):
            raise EOFError("read past end of section")
        self.pos += 8 * n
        return self.data[i:i + n]

    @property
    def byte(self):
        return self.pos >> 3
