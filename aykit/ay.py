"""ZX Spectrum / Amstrad AY music files (ZXAYEMUL): relative pointers, songs, song data, points, memory blocks.

All multi-byte values are big-endian. Pointers are relative: a signed 16-bit
offset measured from the pointer field itself, with 0 meaning "no pointer".
The song list is an array of 4-byte entries (name pointer, song data pointer)
terminated by an entry whose two pointers are both 0.
"""
import struct
from fractions import Fraction

# AY music is replayed once per screen interrupt, i.e. 50 frames per second.
TICK_HZ = 50
# A Z80 address holds at most this many bytes starting at a given address.
Z80_SPACE = 0x10000


class AyError(ValueError):
    pass


def rel(data, at):
    """A relative pointer: signed big-endian 16-bit offset from the pointer field itself; 0 means none.

    The target is returned even if it lies outside the file: callers walking
    tables (the song list terminator in particular) need to see the raw value,
    while the consumers of actual data check the bounds themselves.
    """
    if at is None or at < 0 or at + 2 > len(data):
        return None
    v, = struct.unpack_from(">h", data, at)
    return at + v if v else None


def cstr(data, at):
    if at is None or not 0 <= at < len(data):
        return None
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1") or None


def addr_word(value):
    return "$%04X" % value if value is not None else None


def parse_points(data, at):
    """Player points: stack, init (called first), interrupt, each big-endian."""
    if at is None or at < 0 or at + 6 > len(data):
        return None, None, None
    stack, init, irq = struct.unpack_from(">HHH", data, at)
    return stack, init, irq


def parse_regs(data, at):
    """Initial high/low register pair, big-endian; missing or truncated means unknown."""
    if at is None or at < 0 or at + 4 > len(data):
        return None, None
    hi, lo = struct.unpack_from(">HH", data, at)
    return hi, lo


def parse_blocks(data, at):
    """Walk the memory block table.

    Each entry is six bytes: load address (u16 BE), declared length (u16 BE)
    and a relative pointer to the bytes in the file. An address of 0 ends the
    table. A block only counts when it actually has file data, and the number
    of bytes loaded is capped by both the Z80 address space left above the
    load address and the bytes remaining in the file. Several songs may point
    at the same data; every such block is counted on its own.
    """
    blocks, total = 0, 0
    p = at
    while p is not None and p >= 0 and p + 6 <= len(data):
        addr, length = struct.unpack_from(">HH", data, p)
        if addr == 0:
            break
        src = rel(data, p + 4)
        if src is not None and 0 <= src < len(data):
            space = Z80_SPACE - addr
            file_left = len(data) - src
            loaded = max(0, min(length, space, file_left))
            blocks += 1
            total += loaded
        p += 6
    return blocks, total


def parse(data):
    if data[:8] != b"ZXAYEMUL" or len(data) < 20:
        raise AyError("not a ZXAYEMUL file")
    # The header song field stores the zero-based index of the last song, so
    # the number of songs is one more.
    song_count = data[16] + 1
    out = {"file_version": data[8], "player_version": data[9],
           "author": cstr(data, rel(data, 12)),
           "misc": cstr(data, rel(data, 14)),
           "songs": 0, "first_song": data[17], "song_list": []}
    base = rel(data, 18)
    if base is not None and base >= 0:
        for i in range(song_count):
            s = base + 4 * i
            if s + 4 > len(data):
                break
            # A zero entry ends the list early (songs need not fill the count).
            name_raw, data_raw = struct.unpack_from(">hh", data, s)
            if name_raw == 0 and data_raw == 0:
                break
            name_at = rel(data, s)
            song_at = rel(data, s + 2)
            out["song_list"].append(parse_song(data, name_at, song_at))
    out["songs"] = len(out["song_list"])
    return out


def parse_song(data, name_at, d):
    name = cstr(data, name_at)
    if d is None or d < 0 or d + 14 > len(data):
        return {"name": name, "channels": None, "length_seconds": None,
                "fade_seconds": None, "hi_reg": None, "lo_reg": None,
                "stack": None, "init": None, "interrupt": None,
                "blocks": 0, "block_bytes": 0}
    a, b, c, noise = data[d], data[d + 1], data[d + 2], data[d + 3]
    length, fade = struct.unpack_from(">HH", data, d + 4)
    stack, init, irq = parse_points(data, rel(data, d + 10))
    hi, lo = parse_regs(data, rel(data, d + 8))
    blocks, total = parse_blocks(data, rel(data, d + 12))
    return {
        "name": name,
        "channels": {"a": a, "b": b, "c": c, "noise": noise},
        # A stored length of 0 means the song length is unknown.
        "length_seconds": str(Fraction(length, TICK_HZ)) if length else None,
        "fade_seconds": str(Fraction(fade, TICK_HZ)) if fade else None,
        "hi_reg": hi,
        "lo_reg": lo,
        "stack": addr_word(stack),
        "init": addr_word(init),
        "interrupt": addr_word(irq),
        "blocks": blocks,
        "block_bytes": total,
    }
