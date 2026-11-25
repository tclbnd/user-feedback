"""PICO-8 cartridge images (.p8.png): 160 x 205 RGBA PNG with the cart stored in the low bits."""
import struct
import zlib


class CartError(ValueError):
    pass


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(data):
    """Return (width, height, rgba bytes) of an 8-bit RGBA PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CartError("not a PNG")
    pos, idat, w = 8, b"", None
    while pos + 8 <= len(data):
        n, t = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + n]
        if t == b"IHDR":
            w, h, depth, ctype = struct.unpack_from(">IIBB", body)
            if (depth, ctype) != (8, 6):
                raise CartError("not an RGBA cartridge image")
        elif t == b"IDAT":
            idat += body
        elif t == b"IEND":
            break
        pos += 12 + n
    if w is None:
        raise CartError("no IHDR")
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                line[i] = (line[i] + _paeth(a, b, c)) & 255
        out += line
        prev = line
    return w, h, bytes(out)


def cart_bytes(data):
    w, h, px = read_png(data)
    if (w, h) != (160, 205):
        raise CartError("not a 160 x 205 cartridge image")
    return bytes((px[i + 3] & 3) << 6 | (px[i] & 3) << 4 | (px[i + 1] & 3) << 2 | (px[i + 2] & 3)
                 for i in range(0, len(px), 4))


def parse(data):
    mem = cart_bytes(data)
    hist = [0] * 16
    for b in mem[:0x2000]:
        hist[b & 15] += 1
        hist[b >> 4] += 1
    cells = [mem[0x2000 + y * 128 + x] for y in range(32) for x in range(128)]
    return {"version": mem[0x8000], "gfx_histogram": hist,
            "map_cells": sum(1 for c in cells if c), "map_tiles": len({c for c in cells if c})}
