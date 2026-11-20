"""MSB-first bit reader over a bytes object."""


class BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def read(self, n):
        value = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3] if (self.pos >> 3) < len(self.data) else 0
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def skip(self, n):
        self.pos += n
