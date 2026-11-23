"""Small CD+G files for the tests."""


def packet(inst, data=()):
    d = list(data) + [0] * (16 - len(data))
    return bytes([0x09, inst, 0, 0] + d + [0, 0, 0, 0])


def preset(c=0):
    return packet(1, [c, 0])


def tile(row, col, c0, c1):
    return packet(6, [c0, c1, row, col] + [0x3F] * 12)
