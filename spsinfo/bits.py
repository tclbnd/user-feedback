"""Bit reader with Exp-Golomb codes."""


class BitReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def u(self, n):
        value = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            value = (value << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return value

    def flag(self):
        return self.u(1) == 1

    def ue(self):
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + self.u(zeros)

    def se(self):
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)

    def at_rbsp_trailing(self):
        """True when only an rbsp_stop_one_bit plus alignment zeros remain."""
        if self.pos >= len(self.data) * 8:
            return True
        byte_off = self.pos >> 3
        bit_off = self.pos & 7
        tail = bytearray()
        if bit_off:
            tail.append(self.data[byte_off] & ((1 << (8 - bit_off)) - 1))
            tail.extend(self.data[byte_off + 1:])
        else:
            tail.extend(self.data[byte_off:])
        bits = "".join(format(b, "08b") for b in tail)
        # rbsp_trailing_bits = one '1' followed by zero or more '0'.
        ones = bits.count("1")
        return ones == 1 and bits.rstrip("0").endswith("1")
