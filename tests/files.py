"""A tiny 16-bit little-endian SPHERE file with a 1024-byte header."""


def tiny():
    text = "NIST_1A\n   1024\nsample_count -i 2\nsample_rate -i 8000\nchannel_count -i 1\nend_head\n"
    return text.encode().ljust(1024, b" ") + bytes([1, 0, 2, 0])
