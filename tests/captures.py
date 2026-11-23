"""Small S/PDIF captures for the tests."""
import struct


def ac3_burst(size=1280):
    data = bytes((i * 37) % 251 + 1 for i in range(size))
    words = [0xF872, 0x4E1F, 1, size * 8] + list(struct.unpack("<%dH" % (size // 2), data))
    return words + [0] * (1536 * 2 - len(words))


def wav(words, rate=48000):
    raw = struct.pack("<%dH" % len(words), *words)
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    return b"RIFF" + struct.pack("<I", len(body)) + body
