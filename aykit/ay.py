"""ZX Spectrum / Amstrad AY music files (ZXAYEMUL): pointers, header, songs, player points, memory blocks.

Every multi-byte header, song and player-point field is big-endian.  Song
lengths and fade times are stored in 1/50 s frames.
"""
import struct
from fractions import Fraction

FRAMES_PER_SECOND = 50      # song length and fade are counted in 50 Hz frames
Z80_SIZE = 0x10000          # the 16-bit Z80 address space
HEADER_SIZE = 20
SONG_SIZE = 14              # channels, length, fade, hi/lo, points, blocks
BLOCK_SIZE = 6              # address, length, data pointer


class AyError(ValueError):
    pass


def rel(data, at):
    """A relative pointer: signed big-endian 16-bit offset from the pointer field itself; 0 means none."""
    if at is None or at < 0 or at + 2 > len(data):
        return None
    v, = struct.unpack_from(">h", data, at)
    return at + v if v else None


def cstr(data, at):
    if at is None or at < 0 or at >= len(data):
        return None
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1") or None


def fraction(frames):
    return str(Fraction(frames, FRAMES_PER_SECOND))


def block_summary(data, table):
    """Count the memory blocks and the bytes they actually load.

    A block is loaded only as far as *both* the Z80 address space and the file
    allow, so a declared length can be truncated (even down to nothing).
    """
    blocks = 0
    loaded = 0
    at = table
    while at is not None and at >= 0 and at + BLOCK_SIZE <= len(data):
        addr, length = struct.unpack_from(">HH", data, at)
        if addr == 0:
            break
        src = rel(data, at + 4)
        if src is not None:
            blocks += 1
            if 0 <= src < len(data):
                loaded += min(length, Z80_SIZE - addr, len(data) - src)
        at += BLOCK_SIZE
    return blocks, loaded


def song(data, name_ptr, data_ptr):
    entry = {"name": cstr(data, name_ptr), "channels": None,
             "length_seconds": None, "fade_seconds": None,
             "hi_reg": None, "lo_reg": None,
             "stack": None, "init": None, "interrupt": None,
             "blocks": 0, "block_bytes": 0}
    if data_ptr is None or data_ptr < 0 or data_ptr + SONG_SIZE > len(data):
        return entry

    entry["channels"] = list(data[data_ptr:data_ptr + 4])
    length, fade = struct.unpack_from(">HH", data, data_ptr + 4)
    entry["length_seconds"] = None if length == 0 else fraction(length)
    entry["fade_seconds"] = fraction(fade)
    entry["hi_reg"] = data[data_ptr + 8]
    entry["lo_reg"] = data[data_ptr + 9]

    points = rel(data, data_ptr + 10)
    if points is not None and points >= 0 and points + 6 <= len(data):
        stack, init, interrupt = struct.unpack_from(">HHH", data, points)
        entry["stack"] = "$%04X" % stack
        # A stored init of 0 means the player runs the interrupt routine first.
        entry["init"] = "$%04X" % (init or interrupt)
        entry["interrupt"] = "$%04X" % interrupt

    entry["blocks"], entry["block_bytes"] = block_summary(data, rel(data, data_ptr + 12))
    return entry


def parse(data):
    if data[:8] != b"ZXAYEMUL" or len(data) < HEADER_SIZE:
        raise AyError("not a ZXAYEMUL file")
    out = {"file_version": data[8], "player_version": data[9],
           "author": cstr(data, rel(data, 12)),
           "misc": cstr(data, rel(data, 14)),
           "songs": data[16] + 1, "first_song": data[17], "song_list": []}
    table = rel(data, 18)
    if table is not None:
        for i in range(out["songs"]):
            entry = table + 4 * i
            if entry < 0 or entry + 4 > len(data):
                break
            out["song_list"].append(song(data, rel(data, entry), rel(data, entry + 2)))
    return out
