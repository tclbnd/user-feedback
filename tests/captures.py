"""Small S/PDIF captures for the tests."""
import struct

PA, PB = 0xF872, 0x4E1F


def ac3_burst(size=1280):
    data = bytes((i * 37) % 251 + 1 for i in range(size))
    words = [PA, PB, 1, size * 8] + list(struct.unpack("<%dH" % (size // 2), data))
    return words + [0] * (1536 * 2 - len(words))


def burst(kind, pd, number=0, bsmod=0, error=False, period=1536, seed=1):
    """One burst of `kind` with payload length `pd` (bits, or bytes for E-AC-3)."""
    pc = kind | (((bsmod if kind in (1, 21) else number) & 0x1F) << 8) | (0x80 if error else 0)
    size = pd if kind == 21 else (pd + 7) // 8
    words = [PA, PB, pc, pd] + [(seed * 37 + k * 53) % 0x7FFF + 1 for k in range(size // 2)]
    return words + [0] * max(0, period * 2 - len(words))


def silence(frames):
    return [0] * (frames * 2)


def swap_words(words):
    """Store each 16-bit word byte-swapped, as some capture tools do."""
    return list(struct.unpack("<%dH" % len(words), struct.pack(">%dH" % len(words), *words)))


def wav(words, rate=48000):
    raw = struct.pack("<%dH" % len(words), *words)
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(raw)) + raw
    return b"RIFF" + struct.pack("<I", len(body)) + body
