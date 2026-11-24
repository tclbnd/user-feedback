"""A tiny THP 1.0 movie for the tests: one video component, equal-size frames."""
import struct


def movie(frames=3, video=992, width=320, height=240):
    comp = struct.pack(">I", 1) + bytes([0]) + b"\xff" * 15 + struct.pack(">II", width, height)
    size = 8 + 4 + video
    head = b"THP\0" + struct.pack(">III", 0x00010000, size, 0) + struct.pack(">I", 0)
    first = 0x30 + len(comp)
    first += -first % 32
    head += struct.pack(">7I", frames, size, size * frames, 0x30, 0, first, first + size * (frames - 1))
    body = b"".join(struct.pack(">III", size, size, video) + bytes(video) for _ in range(frames))
    return head + comp + bytes(first - 0x30 - len(comp)) + body
