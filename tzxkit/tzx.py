"""ZX Spectrum TZX tape images: block list, playing time at 3.5 MHz, standard file headers, archive info."""
import struct
from fractions import Fraction

CLOCK = 3500000
KINDS = ("program", "number_array", "character_array", "bytes")
INFO = {0: "title", 1: "publisher", 2: "author", 3: "year", 4: "language", 5: "type", 6: "price",
        7: "loader", 8: "origin", 0xFF: "comment"}


class TzxError(ValueError):
    pass


def u24(data, p):
    return int.from_bytes(data[p:p + 3], "little")


def bits_time(block, zero, one, used=8):
    """T-states of a data stream: two pulses per bit, only `used` bits of the last byte."""
    t = 0
    last = len(block) - 1
    for i, b in enumerate(block):
        n = used if i == last else 8
        for k in range(n):
            t += 2 * (one if b >> (7 - k) & 1 else zero)
    return t


def samples_time(block, sample_tstates, used=8):
    """T-states for a direct recording: one timed sample per represented bit."""
    if not block:
        return 0
    return (8 * (len(block) - 1) + used) * sample_tstates


def parse(data):
    if data[:8] != b"ZXTape!\x1a" or len(data) < 10:
        raise TzxError("not a TZX file")
    out = {"version": "%d.%02d" % (data[8], data[9]), "blocks": 0, "block_types": {}, "files": [],
           "checksum_errors": 0, "stops": 0, "archive": None, "texts": []}
    total_tstates = 0
    total_ms = 0
    loop_counts = []
    p = 10
    while p < len(data):
        bid = data[p]
        p += 1
        t, ms = 0, 0

        if bid == 0x10:  # Standard speed data block
            ms, n = struct.unpack_from("<HH", data, p)
            block = data[p + 4:p + 4 + n]
            p += 4 + n

            # TZX uses the flag byte's top bit to select the ROM pilot length.
            # This also covers non-standard low flag bytes which use the long pilot.
            pilot_pulses = 8063 if block and block[0] < 128 else 3223
            t = pilot_pulses * 2168 + 667 + 735 + bits_time(block, 855, 1710)

            x = 0
            for b in block:
                x ^= b
            if block and x:
                out["checksum_errors"] += 1

            if len(block) == 19 and block[0] == 0:
                kind = block[1]
                length, param = struct.unpack_from("<HH", block, 12)
                out["files"].append({"type": KINDS[kind] if kind < 4 else "type_%d" % kind,
                                     "name": block[2:12].decode("latin-1").rstrip(),
                                     "length": length, "param": param})

        elif bid == 0x11:  # Turbo speed data block
            pilot_len, sync1, sync2, zero_len, one_len, pilot_pulses = \
                struct.unpack_from("<HHHHHH", data, p)
            used = data[p + 12]
            ms = struct.unpack_from("<H", data, p + 13)[0]
            n = u24(data, p + 15)
            block = data[p + 18:p + 18 + n]
            p += 18 + n
            t = (pilot_pulses * pilot_len + sync1 + sync2
                 + bits_time(block, zero_len, one_len, used))

        elif bid == 0x12:  # Pure tone
            pulse_len, pulse_count = struct.unpack_from("<HH", data, p)
            p += 4
            t = pulse_len * pulse_count

        elif bid == 0x13:  # Pulse sequence
            count = data[p]
            p += 1
            for i in range(count):
                t += struct.unpack_from("<H", data, p + 2 * i)[0]
            p += 2 * count

        elif bid == 0x14:  # Pure data block
            zero_len, one_len = struct.unpack_from("<HH", data, p)
            used = data[p + 4]
            ms = struct.unpack_from("<H", data, p + 5)[0]
            n = u24(data, p + 7)
            block = data[p + 10:p + 10 + n]
            p += 10 + n
            t = bits_time(block, zero_len, one_len, used)

        elif bid == 0x15:  # Direct recording block
            sample_len, ms = struct.unpack_from("<HH", data, p)
            used = data[p + 4]
            n = struct.unpack_from("<I", data, p + 5)[0]
            block = data[p + 9:p + 9 + n]
            p += 9 + n
            t = samples_time(block, sample_len, used)

        elif bid == 0x20:  # Pause or stop-the-tape command
            ms, = struct.unpack_from("<H", data, p)
            p += 2
            if ms == 0:
                out["stops"] += 1

        elif bid == 0x21:  # Group start
            n = data[p]
            p += 1 + n

        elif bid == 0x22:  # Group end
            pass

        elif bid == 0x23:  # Jump to block; durations are summed linearly
            p += 2

        elif bid == 0x24:  # Loop start
            count, = struct.unpack_from("<H", data, p)
            p += 2
            loop_counts.append(count)

        elif bid == 0x25:  # Loop end
            if loop_counts:
                loop_counts.pop()

        elif bid == 0x26:  # Call sequence
            count, = struct.unpack_from("<H", data, p)
            p += 2 + 2 * count

        elif bid == 0x27:  # Return from sequence
            pass

        elif bid == 0x28:  # Select block
            n, = struct.unpack_from("<H", data, p)
            p += 2 + n

        elif bid == 0x2A:  # Stop the tape if in 48K mode
            n, = struct.unpack_from("<I", data, p)
            p += 4 + n
            out["stops"] += 1

        elif bid == 0x2B:  # Set signal level
            n, = struct.unpack_from("<I", data, p)
            p += 4 + n

        elif bid == 0x30:  # Text description
            n = data[p]
            out["texts"].append(data[p + 1:p + 1 + n].decode("latin-1"))
            p += 1 + n

        elif bid == 0x31:  # Message block
            n = data[p + 1]
            p += 2 + n

        elif bid == 0x32:  # Archive info
            n, = struct.unpack_from("<H", data, p)
            body = data[p + 2:p + 2 + n]
            p += 2 + n
            info, q = {}, 1
            for _ in range(body[0]):
                i, ln = body[q], body[q + 1]
                info[INFO.get(i, "info_%02x" % i)] = body[q + 2:q + 2 + ln].decode("latin-1")
                q += 2 + ln
            out["archive"] = info

        elif bid == 0x33:  # Hardware type
            count = data[p]
            p += 1 + 3 * count

        elif bid == 0x34:  # Emulation information
            p += 8

        elif bid == 0x35:  # Custom information block
            n, = struct.unpack_from("<I", data, p + 16)
            p += 20 + n

        elif bid in (0x18, 0x19):  # CSW recording / generalized data: length-prefixed chunks
            n, = struct.unpack_from("<I", data, p)
            p += 4 + n

        elif bid == 0x5A:  # Glue block
            p += 9

        else:
            raise TzxError("unknown block 0x%02X at %d" % (bid, p - 1))

        out["blocks"] += 1
        key = "0x%02X" % bid
        out["block_types"][key] = out["block_types"].get(key, 0) + 1

        loop_multiplier = 1
        for count in loop_counts:
            loop_multiplier *= count
        total_tstates += t * loop_multiplier
        total_ms += ms * loop_multiplier

    out["duration"] = str(Fraction(total_tstates, CLOCK) + Fraction(total_ms, 1000))
    return out
