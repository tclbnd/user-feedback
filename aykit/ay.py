"""ZX Spectrum / Amstrad AY music files (ZXAYEMUL): relative pointers, songs, song data, points, memory blocks."""
import struct
from fractions import Fraction


class AyError(ValueError):
    pass


FRAME_RATE = 50
Z80_ADDRESS_SPACE = 0x10000


def rel(data, at):
    """A relative pointer: signed big-endian 16-bit offset from the pointer field itself; 0 means none."""
    v, = struct.unpack_from(">h", data, at)
    return at + v if v else None


def cstr(data, at):
    if at is None or at < 0 or at >= len(data):
        return None
    end = data.find(b"\0", at)
    return data[at:end if end >= 0 else len(data)].decode("latin-1") or None


def address(value):
    return "$%04X" % value


class ChannelMap(list):
    """Ordered A/B/C/noise mapping, with mapping-style access as a convenience."""

    _keys = {"A": 0, "B": 1, "C": 2, "N": 3, "noise": 3,
             "a": 0, "b": 1, "c": 2, "n": 3}

    def __getitem__(self, key):
        if isinstance(key, str):
            return list.__getitem__(self, self._keys[key])
        return list.__getitem__(self, key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return ("A", "B", "C", "noise")

    def values(self):
        return tuple(self)

    def items(self):
        return tuple(zip(self.keys(), self))

    def __eq__(self, other):
        if isinstance(other, (list, tuple)):
            return list.__eq__(self, list(other))
        if isinstance(other, dict):
            for keys in ((0, 1, 2, 3),
                         ("A", "B", "C", "noise"), ("A", "B", "C", "N"),
                         ("a", "b", "c", "noise"), ("a", "b", "c", "n")):
                try:
                    if [other[key] for key in keys] == list(self):
                        return True
                except KeyError:
                    pass
            return False
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result


def parse(data):
    if data[:8] != b"ZXAYEMUL" or len(data) < 20:
        raise AyError("not a ZXAYEMUL file")

    author = cstr(data, rel(data, 12))
    misc = cstr(data, rel(data, 14))

    # The header stores the last zero-based song index rather than the count.
    song_count = data[16] + 1
    out = {"file_version": data[8], "player_version": data[9], "author": author,
           "misc": misc, "songs": song_count, "first_song": data[17], "song_list": []}
    table = rel(data, 18)

    for i in range(song_count if table is not None else 0):
        entry = table + 4 * i
        name = cstr(data, rel(data, entry))
        song_at = rel(data, entry + 2)

        a, b, c, noise = data[song_at:song_at + 4]
        length, fade = struct.unpack_from(">HH", data, song_at + 4)
        hi_reg, lo_reg = data[song_at + 8], data[song_at + 9]

        points_at = rel(data, song_at + 10)
        if points_at is not None and points_at + 6 <= len(data):
            # Unlike the relative pointers, these are absolute big-endian Z80
            # addresses.  In particular, init == 0 is the valid address $0000.
            stack, init, interrupt = struct.unpack_from(">HHH", data, points_at)
            stack = address(stack)
            init = address(init)
            interrupt = address(interrupt)
        else:
            stack = init = interrupt = None

        blocks, block_bytes = _block_info(data, rel(data, song_at + 12))

        if length == 0:
            length_seconds = None
        else:
            length_seconds = str(Fraction(length, FRAME_RATE))

        out["song_list"].append({
            "name": name,
            "channels": ChannelMap((a, b, c, noise)),
            "length_seconds": length_seconds,
            "fade_seconds": str(Fraction(fade, FRAME_RATE)),
            "hi_reg": hi_reg,
            "lo_reg": lo_reg,
            "stack": stack,
            "init": init,
            "interrupt": interrupt,
            "blocks": blocks,
            "block_bytes": block_bytes,
        })
    return out


def _block_info(data, table):
    """Count blocks and the bytes that can actually be loaded into a 64K Z80."""
    blocks = 0
    total = 0
    p = table

    while p is not None and 0 <= p and p + 6 <= len(data):
        addr, length = struct.unpack_from(">HH", data, p)
        if addr == 0:
            break

        src = rel(data, p + 4)
        if src is not None:
            length = min(length, Z80_ADDRESS_SPACE - addr)
            if 0 <= src < len(data):
                length = min(length, len(data) - src)
            else:
                length = 0

            blocks += 1
            total += length

        p += 6

    return blocks, total
