"""MSB-first bit reader/writer."""


class Reader:
    def __init__(self, data, pos=0, end=None):
        self.data, self.pos, self.end = data, pos, end

    def _bits_available(self):
        n = 8 * len(self.data)
        if self.end is not None and self.end < n:
            n = self.end
        return n

    def u(self, n):
        if self.pos + n > self._bits_available():
            raise EOFError("read past end")
        v = 0
        for _ in range(n):
            i = self.pos >> 3
            v = (v << 1) | ((self.data[i] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def skip(self, n):
        if self.pos + n > self._bits_available():
            raise EOFError("read past end")
        self.pos += n

    def bits_left(self):
        return self._bits_available() - self.pos
