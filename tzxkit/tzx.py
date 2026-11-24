"""ZX Spectrum TZX tape images: block list, playing time at 3.5 MHz, standard file headers, archive info."""
import struct
from fractions import Fraction

CLOCK = 3500000
KINDS = ("program", "number_array", "character_array", "bytes")
INFO = {0: "title", 1: "author", 2: "publisher", 3: "year", 4: "language", 5: "type", 6: "price", 7: "loader",
        8: "origin", 0xFF: "comment"}


class TzxError(ValueError):
    pass


def u24(data, p):
    return int.from_bytes(data[p:p + 3], "little")


def bits_time(block, zero, one, used):
    """T-states of a data stream: two pulses per bit, only `used` bits of the last byte."""
    t = 0
    for i, b in enumerate(block):
        n = used if i == len(block) - 1 else 8
        for k in range(n):
            t += 2 * (zero if b >> (7 - k) & 1 else one)
    return t


def parse(data):
    if data[:8] != b"ZXTape!\x1a" or len(data) < 10:
        raise TzxError("not a TZX file")
    out = {"version": "%d.%02d" % (data[8], data[9]), "blocks": 0, "block_types": {}, "files": [],
           "checksum_errors": 0, "stops": 0, "archive": None, "texts": []}
    timeline = []                                         # (T-states, pause ms) per block, loops expanded below
    p = 10
    while p < len(data):
        bid = data[p]
        p += 1
        t, ms = 0, 0
        if bid == 0x10:
            ms, n = struct.unpack_from("<HH", data, p)
            block = data[p + 4:p + 4 + n]
            p += 4 + n
            pilot = 8063
            t = pilot * 2168 + 667 + 735 + bits_time(block, 855, 1710, 8)
            x = 0
            for b in block:
                x ^= b
            if block and x:
                out["checksum_errors"] += 1
            if len(block) == 19 and block[0] == 0:
                kind = block[1]
                length, p1, p2 = struct.unpack_from(">HHH", block, 12)
                out["files"].append({"type": KINDS[kind] if kind < 4 else "type_%d" % kind,
                                     "name": block[2:12].decode("latin-1").rstrip(), "length": length,
                                     "param": p1})
        elif bid == 0x20:
            ms, = struct.unpack_from("<H", data, p)
            p += 2
        elif bid == 0x30:
            out["texts"].append(data[p + 1:p + 1 + data[p]].decode("latin-1"))
            p += 1 + data[p]
        elif bid == 0x32:
            n = data[p]
            body = data[p + 2:p + 2 + n]
            p += 2 + n
            info, q = {}, 1
            for _ in range(body[0]):
                i, ln = body[q], body[q + 1]
                info[INFO.get(i, "info_%02x" % i)] = body[q + 2:q + 2 + ln].decode("latin-1")
                q += 2 + ln
            out["archive"] = info
        else:
            raise TzxError("unknown block 0x%02X at %d" % (bid, p - 1))
        out["blocks"] += 1
        key = "0x%02X" % bid
        out["block_types"][key] = out["block_types"].get(key, 0) + 1
        timeline.append((t, ms))
    out["duration"] = str(sum((Fraction(t, CLOCK) + Fraction(ms, 1000) for t, ms in timeline), Fraction(0)))
    return out
