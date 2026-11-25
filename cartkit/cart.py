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


def _music(mem):
    """Summarise the 64 music patterns at 0x3100 (4 channel bytes each).

    In a channel byte bits 0-5 hold the sound effect number, bit 6 marks the
    channel as disabled and bit 7 carries the pattern flag: loop start on
    channel 0, loop end on channel 1, stop on channel 2.
    """
    patterns = 0
    music_sfx = set()
    loop_starts = loop_ends = stops = 0
    for i in range(64):
        ch = mem[0x3100 + i * 4:0x3100 + i * 4 + 4]
        enabled = [c for c in ch if not c & 0x40]
        if enabled:
            patterns += 1
            music_sfx |= {c & 0x3F for c in enabled}
        loop_starts += bool(ch[0] & 0x80)
        loop_ends += bool(ch[1] & 0x80)
        stops += bool(ch[2] & 0x80)
    return patterns, sorted(music_sfx), loop_starts, loop_ends, stops


def _sfx(mem):
    """Summarise the 64 sound effects at 0x3200 (68 bytes each).

    Each effect is 32 two-byte notes followed by editor mode, speed, loop
    start and loop end.  In a note bits 0-5 are pitch, 6-8 waveform, 9-11
    volume, 12-14 effect and bit 15 selects a custom instrument.
    """
    sfx_used = notes = custom_notes = looping_sfx = 0
    waveforms, effects, pitches = [0] * 8, [0] * 8, []
    for i in range(64):
        base = 0x3200 + i * 0x44
        looping_sfx += mem[base + 0x43] > mem[base + 0x42]
        used = False
        for j in range(32):
            word = mem[base + 2 * j] | mem[base + 2 * j + 1] << 8
            if not (word >> 9) & 7:      # volume 0: silent note
                continue
            used = True
            notes += 1
            pitches.append(word & 0x3F)
            effects[(word >> 12) & 7] += 1
            if word & 0x8000:
                custom_notes += 1
            else:
                waveforms[(word >> 6) & 7] += 1
        sfx_used += used
    return (sfx_used, notes, waveforms, custom_notes, effects,
            [min(pitches), max(pitches)] if pitches else None, looping_sfx)


def parse(data):
    mem = cart_bytes(data)
    hist = [0] * 16
    for b in mem[:0x2000]:
        hist[b & 15] += 1
        hist[b >> 4] += 1
    # The 128 x 64 map is split in memory: rows 0-31 at 0x2000, rows 32-63
    # share the second half of the sprite sheet at 0x1000.
    cells = mem[0x2000:0x3000] + mem[0x1000:0x2000]
    patterns, music_sfx, loop_starts, loop_ends, stops = _music(mem)
    sfx_used, notes, waveforms, custom_notes, effects, pitch_range, looping_sfx = _sfx(mem)
    return {"version": mem[0x8000], "gfx_histogram": hist,
            "flagged_sprites": sum(1 for b in mem[0x3000:0x3100] if b),
            "map_cells": sum(1 for c in cells if c),
            "map_tiles": len({c for c in cells if c}),
            "sfx_used": sfx_used, "notes": notes, "waveforms": waveforms,
            "custom_notes": custom_notes, "effects": effects,
            "pitch_range": pitch_range, "looping_sfx": looping_sfx,
            "patterns": patterns, "music_sfx": music_sfx,
            "loop_starts": loop_starts, "loop_ends": loop_ends, "stops": stops}
