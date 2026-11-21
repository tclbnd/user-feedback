"""Builders for small synthetic XM modules used by the tests."""
import struct


def pattern(rows=64, grid=None, channels=4):
    """Unpacked pattern: 5 bytes (note, instrument, volume, effect, parameter) per cell."""
    grid = grid or {}
    body = b"".join(bytes(grid.get((r, c), (0, 0, 0, 0, 0))) for r in range(rows) for c in range(channels))
    return struct.pack("<IBHH", 9, 0, rows, len(body)) + body


def sample(name="s", frames=100, bits=8, loop=False, loop_start=0, loop_length=0, volume=64):
    w = 2 if bits == 16 else 1
    typ = (1 if loop else 0) | (0x10 if bits == 16 else 0)
    head = struct.pack("<IIIBbBBbB", frames * w, loop_start * w, loop_length * w, volume, 0, typ, 128, 0, 0)
    return head + name.encode("latin-1").ljust(22, b"\0"), bytes(((k * 3) & 0x7F) for k in range(frames * w))


def instrument(name="inst", samples=()):
    head = struct.pack("<I", 263) + name.encode("latin-1").ljust(22, b"\0") + bytes([0]) + struct.pack("<HI", len(samples), 40)
    head += bytes(263 - len(head))
    return head + b"".join(h for h, _ in samples) + b"".join(d for _, d in samples)


def module(name="song", channels=4, patterns=(), instruments=(), orders=(0,), tempo=6, bpm=125):
    head = b"Extended Module: " + name.encode("latin-1").ljust(20, b"\0") + b"\x1a" + b"FastTracker v2.00".ljust(20, b"\0")
    head += struct.pack("<HI", 0x0104, 276)
    head += struct.pack("<8H", len(orders), 0, channels, len(patterns), len(instruments), 1, tempo, bpm)
    head += bytes(list(orders) + [0] * (256 - len(orders)))
    return head + b"".join(patterns) + b"".join(instruments)
