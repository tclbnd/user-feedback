"""Minimal GBS rips for the tests."""
import struct


def rip(songs=3, title=b"Test", first_song=1, load=0x4000, init=0x4000,
        play=0x4010, stack=0xFFFE, tma=0, tac=0, code=64):
    h = bytearray(0x70)
    h[:3] = b"GBS"
    h[3], h[4], h[5] = 1, songs, first_song
    struct.pack_into("<HHHH", h, 6, load, init, play, stack)
    h[0x0E], h[0x0F] = tma, tac
    h[0x10:0x10 + len(title)] = title
    return bytes(h) + bytes(code)
