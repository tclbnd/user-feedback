"""A minimal IT module for the tests: no orders, samples or patterns."""


def minimal(title=b"Test"):
    h = bytearray(192)
    h[:4] = b"IMPM"
    h[4:4 + len(title)] = title
    h[48:53] = bytes([128, 48, 6, 125, 128])
    return bytes(h)
