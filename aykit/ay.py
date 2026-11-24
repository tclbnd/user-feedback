"""ZX Spectrum / Amstrad AY music files (ZXAYEMUL): relative pointers, songs, song data, points, memory blocks."""
import struct
from fractions import Fraction

HEADER_SIZE = 20
# Song and fade lengths are stored in units of 1/50 s (the Spectrum frame rate).
FRAMES_PER_SECOND = 50
TOP_OF_MEMORY = 0x10000


class AyError(ValueError):
    pass


def rel(data, at):
    """A relative pointer: signed big-endian 16-bit offset from the pointer field itself; 0 means none."""
    if at is None or at < 0 or at + 2 > len(data):
        return None
    v, = struct.unpack_from(">h", data, at)
    target = at + v
    return target if v and 0 <= target < len(data) else None


def cstr(data, at):
    if at is None or at >= len(data):
        return None
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1") or None


def z80(addr):
    """A Z80 address as printed in the output."""
    return None if addr is None else "$%04X" % addr


def seconds(frames):
    return str(Fraction(frames, FRAMES_PER_SECOND))


def block_bytes(data, src, length, load_addr):
    """How much of a memory block is really loaded: the Z80 address space and the file both cap it."""
    if src is None:
        return 0
    return max(0, min(length, TOP_OF_MEMORY - load_addr, len(data) - src))


def load_blocks(data, at):
    """Walk a memory block table: (load address, length, data pointer) triples until address 0."""
    blocks = total = 0
    p = at
    while p is not None and p + 6 <= len(data):
        load_addr, length = struct.unpack_from(">HH", data, p)
        if load_addr == 0:
            break
        src = rel(data, p + 4)
        size = block_bytes(data, src, length, load_addr)
        if size:
            blocks += 1
            total += size
        p += 6
    return blocks, total


def player_points(data, at):
    """Stack, init and interrupt addresses; the player's first call is the init routine."""
    if at is None or at + 6 > len(data):
        return None, None, None
    stack, init, interrupt = struct.unpack_from(">HHH", data, at)
    # Files may leave the init address empty; playback then starts at the interrupt routine.
    first = init or interrupt
    return z80(stack), z80(first), z80(interrupt)


def song(data, entry):
    """One song list entry and the song data it points at.  Songs without player points still parse."""
    name = cstr(data, rel(data, entry))
    out = {"name": name, "channels": None, "length_seconds": None, "fade_seconds": None,
           "hi_reg": None, "lo_reg": None, "stack": None, "init": None, "interrupt": None,
           "blocks": 0, "block_bytes": 0}
    d = rel(data, entry + 2)
    if d is None or d + 14 > len(data):
        return out
    out["channels"] = list(data[d:d + 4])
    length, fade = struct.unpack_from(">HH", data, d + 4)
    out["hi_reg"], out["lo_reg"] = data[d + 8], data[d + 9]
    # A zero length means the song length is unknown.
    out["length_seconds"] = seconds(length) if length else None
    out["fade_seconds"] = seconds(fade)
    out["stack"], out["init"], out["interrupt"] = player_points(data, rel(data, d + 10))
    out["blocks"], out["block_bytes"] = load_blocks(data, rel(data, d + 12))
    return out


def parse(data):
    if data[:8] != b"ZXAYEMUL" or len(data) < HEADER_SIZE:
        raise AyError("not a ZXAYEMUL file")
    out = {"file_version": data[8], "player_version": data[9],
           "author": cstr(data, rel(data, 12)), "misc": cstr(data, rel(data, 14)),
           # The stored song count is one less than the number of song list entries.
           "songs": data[16] + 1, "first_song": data[17], "song_list": []}
    base = rel(data, 18)
    for i in range(out["songs"]):
        entry = base + 4 * i if base is not None else None
        if entry is None or entry + 4 > len(data):
            break
        out["song_list"].append(song(data, entry))
    return out
