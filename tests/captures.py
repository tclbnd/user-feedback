"""Builders for small synthetic SBC / mSBC captures used by the tests."""
from math import ceil

BLK = {4: 0, 8: 1, 12: 2, 16: 3}
H2 = (0x08, 0x38, 0xC8, 0xF8)


def length(mode, subbands, blocks, bitpool):
    ch = 1 if mode == 0 else 2
    if mode in (0, 1):
        return 4 + (4 * subbands * ch) // 8 + ceil(blocks * ch * bitpool / 8)
    return 4 + (4 * subbands * ch) // 8 + ceil(((subbands if mode == 3 else 0) + blocks * bitpool) / 8)


def frame(rate=2, blocks=16, mode=2, subbands=8, bitpool=53, alloc=0, fill=0x55, n=None):
    b1 = (rate << 6) | (BLK[blocks] << 4) | (mode << 2) | (alloc << 1) | (1 if subbands == 8 else 0)
    n = length(mode, subbands, blocks, bitpool) if n is None else n
    return bytes([0x9C, b1, bitpool, 0]) + bytes([fill]) * (n - 4)


def msbc(fill=0x33):
    return bytes([0xAD, 0, 0, 0]) + bytes([fill]) * 53


def h2(seq, frame_=None):
    return bytes([0x01, H2[seq % 4]]) + (frame_ or msbc()) + b"\x00"


def payload(frames, F=0, S=0, L=0, n=None):
    n = len(frames) if n is None else n
    return bytes([(F << 7) | (S << 6) | (L << 5) | n]) + b"".join(frames)


def a2dp(payloads):
    return b"".join(len(p).to_bytes(2, "big") + p for p in payloads)
