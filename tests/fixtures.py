"""Deterministic APNG fixtures and an independent sequential compositor.

The reference compositor here is written from the APNG specification instead
of reusing ``view.compose``: it keeps a per-pixel tuple model, applies disposal
one frame at a time and rounds with a plain integer formula, so it can
cross-check the viewer's optimised code paths.
"""
import random
import struct
import zlib
from fractions import Fraction

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body)))


def raw_rows(width, height, pixel_at):
    """Build scanlines with a mix of filter types so filtering is exercised."""
    out = bytearray()
    previous = bytes(4 * width)
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend(pixel_at(x, y))
        mode = (y + width) % 5
        filtered = bytearray(4 * width)
        for i in range(4 * width):
            a = row[i - 4] if i >= 4 else 0
            b = previous[i]
            c = previous[i - 4] if i >= 4 else 0
            if mode == 1:
                v = a
            elif mode == 2:
                v = b
            elif mode == 3:
                v = (a + b) // 2
            elif mode == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                v = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            else:
                v = 0
            filtered[i] = (row[i] - v) % 256
        out.append(mode)
        out.extend(filtered)
        previous = bytes(row)
    return bytes(out)


def compress(width, height, pixel_at):
    return zlib.compress(raw_rows(width, height, pixel_at))


def fctl(seq, width, height, x, y, delay_num, delay_den, dispose, blend):
    return chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, width, height, x, y,
                                      delay_num, delay_den, dispose, blend))


def build(width, height, frames, loops=0, default_image=False):
    """Assemble an APNG from frame dictionaries.

    Each frame needs x, y, w, h, pixels(x, y) -> 4 bytes and may set
    delay_num (1), delay_den (25), dispose (0) and blend (0). With
    ``default_image`` the first frame is stored as fdAT and a distinct
    still image occupies the IDAT slot.
    """
    png = bytearray(SIGNATURE)
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"acTL", struct.pack(">II", len(frames), loops))
    if default_image:
        # A still image that is not part of the animation. It occupies the
        # IDAT slot before any fcTL, so animation frames all arrive as fdAT.
        png += chunk(b"IDAT", compress(
            width, height, lambda x, y: bytes((9, 9, 9, 255))))
    seq = 0
    for i, frame in enumerate(frames):
        png += fctl(seq, frame["w"], frame["h"], frame["x"], frame["y"],
                    frame.get("delay_num", 1), frame.get("delay_den", 25),
                    frame.get("dispose", 0), frame.get("blend", 0))
        seq += 1
        data = compress(frame["w"], frame["h"], frame["pixels"])
        if i == 0 and not default_image:
            png += chunk(b"IDAT", data)
        else:
            png += chunk(b"fdAT", struct.pack(">I", seq) + data)
            seq += 1
    png += chunk(b"IEND", b"")
    return bytes(png)


# --- independent reference implementation -------------------------------

def _round_half_up(numerator, denominator):
    return (2 * numerator + denominator) // (2 * denominator)


def _over(src, dst):
    sa = src[3]
    if sa == 0:
        return dst
    if sa == 255:
        return src
    da = dst[3]
    da_part = da * (255 - sa)
    den = sa * 255 + da_part

    def mix(s, d):
        return _round_half_up(s * sa * 255 + d * da_part, den)

    alpha = _round_half_up(den, 255)
    return (mix(src[0], dst[0]), mix(src[1], dst[1]), mix(src[2], dst[2]),
            alpha)


def reference_frames(width, height, frames, with_after=False):
    """Sequential composition as a straightforward list-of-tuples model."""
    canvas = [[(0, 0, 0, 0)] * width for _ in range(height)]
    displays, afters = [], []
    for index, frame in enumerate(frames):
        before = [row[:] for row in canvas]
        draw = [row[:] for row in before]
        for fy in range(frame["h"]):
            for fx in range(frame["w"]):
                src = tuple(frame["pixels"](fx, fy))
                py, px = frame["y"] + fy, frame["x"] + fx
                if frame.get("blend", 0) == 0:
                    draw[py][px] = src
                else:
                    draw[py][px] = _over(src, draw[py][px])
        displays.append(draw)
        dispose = frame.get("dispose", 0)
        if index == 0 and dispose == 2:
            dispose = 1
        if dispose == 1:
            after = [row[:] for row in draw]
            for fy in range(frame["h"]):
                for fx in range(frame["w"]):
                    after[frame["y"] + fy][frame["x"] + fx] = (0, 0, 0, 0)
        elif dispose == 2:
            after = [row[:] for row in draw]
            for fy in range(frame["h"]):
                for fx in range(frame["w"]):
                    py, px = frame["y"] + fy, frame["x"] + fx
                    after[py][px] = before[py][px]
        else:
            after = draw
        canvas = after
        afters.append(after)
    return (displays, afters) if with_after else displays


def rgba_bytes(display_rows):
    flat = bytearray()
    for row in display_rows:
        for pixel in row:
            flat.extend(pixel)
    return bytes(flat)


def starts(frames):
    times, total = [], Fraction(0, 1)
    for frame in frames:
        times.append(total)
        total += Fraction(frame.get("delay_num", 1),
                          frame.get("delay_den", 25) or 100)
    return times


def tricky_fixture(width=7, height=5, count=120, seed=7):
    """120 frames where one pixel sees long PREVIOUS/BACKGROUND chains.

    Every frame after the first covers pixel (2, 2), and disposal follows a
    9-frame pattern with runs of consecutive PREVIOUS and consecutive
    BACKGROUND frames. The pattern length does not divide the checkpoint
    spacing, so checkpoints land on frames with different disposal modes.
    """
    rng = random.Random(seed)
    disposal_pattern = [2, 2, 0, 1, 1, 2, 2, 2, 1]
    frames = [{
        "x": 0, "y": 0, "w": width, "h": height,
        "pixels": lambda x, y: bytes((30 + 3 * x, 40 + 5 * y, 90, 255)),
        "delay_num": 1, "delay_den": 25, "dispose": 0, "blend": 0,
    }]
    shapes = [
        {"x": 1, "y": 1, "w": 4, "h": 3},
        {"x": 2, "y": 1, "w": 3, "h": 3},
        {"x": 1, "y": 2, "w": 5, "h": 2},
        {"x": 2, "y": 2, "w": 2, "h": 2},
        {"x": 1, "y": 1, "w": 5, "h": 2},
        {"x": 2, "y": 2, "w": 3, "h": 2},
    ]
    for i in range(1, count):
        rect = dict(shapes[i % len(shapes)])
        colour = (rng.randrange(256), rng.randrange(256),
                  rng.randrange(256), rng.choice((0, 64, 128, 200, 255)))
        fw, fh = rect["w"], rect["h"]
        rect["dispose"] = disposal_pattern[i % len(disposal_pattern)]
        rect["blend"] = popcount(i) % 3 % 2

        def make(x, y, colour=colour, i=i, fw=fw, fh=fh):
            # Nearly uniform rectangles with a moving notch, so pixel (2, 2)
            # is rewritten by almost every frame while disposal keeps
            # restoring or clearing it underneath.
            if (x, y) == (i % fw, (i // fw) % fh):
                return bytes((255 - colour[0], colour[1], 0, 255))
            return bytes(colour)

        rect["pixels"] = make
        rect["delay_num"] = (i % 3) + 1
        rect["delay_den"] = 100 if i % 2 else 25
        frames.append(rect)
    return frames


def popcount(value):
    return bin(value).count("1")
