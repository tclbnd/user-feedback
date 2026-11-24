"""ZX Spectrum TZX tape images: block list, playing time at 3.5 MHz, standard file headers and archive info."""
import struct
from fractions import Fraction

CLOCK = 3500000
KINDS = ("program", "number_array", "character_array", "bytes")
INFO = {0: "title", 1: "publisher", 2: "author", 3: "year", 4: "language", 5: "type", 6: "price",
        7: "loader", 8: "origin", 0xFF: "comment"}

HEADER_PILOT_PULSES = 8063
DATA_PILOT_PULSES = 3223
PILOT_LENGTH = 2168
SYNC1_LENGTH = 667
SYNC2_LENGTH = 735
ZERO_LENGTH = 855
ONE_LENGTH = 1710


class TzxError(ValueError):
    pass


def u24(data, p):
    return int.from_bytes(data[p:p + 3], "little")


def u32(data, p):
    return struct.unpack_from("<I", data, p)[0]


def bits_time(block, zero, one, used):
    """T-states of a data stream: two pulses per bit, only `used` bits of the last byte."""
    t = 0
    last = len(block) - 1
    for i, b in enumerate(block):
        if i == last:
            n = used
            if n <= 0:
                continue
            if n < 8:
                b = (b >> (8 - n)) & ((1 << n) - 1)
            else:
                n = 8
        else:
            n = 8

        ones = bin(b).count("1")
        t += 2 * (ones * one + (n - ones) * zero)
    return t


def _skip_stop_48k_suffix(data, p):
    """Skip reserved zero length fields, while also accepting a bare block."""
    if p + 4 <= len(data) and u32(data, p) == 0:
        return p + 4
    if p + 2 <= len(data) and struct.unpack_from("<H", data, p)[0] == 0:
        return p + 2
    if p < len(data) and data[p] == 0:
        return p + 1
    return p


def parse(data):
    if data[:8] != b"ZXTape!\x1a" or len(data) < 10:
        raise TzxError("not a TZX file")

    out = {"version": "%d.%02d" % (data[8], data[9]), "blocks": 0, "block_types": {}, "files": [],
           "checksum_errors": 0, "stops": 0, "archive": None, "texts": []}

    p = 10
    total_states = 0
    total_pause_ms = 0
    loop_multiplier = 1
    loop_stack = []

    while p < len(data):
        bid = data[p]
        p += 1
        states = 0
        pause_ms = 0

        if bid == 0x10:  # Standard speed data
            pause_ms, n = struct.unpack_from("<HH", data, p)
            block = data[p + 4:p + 4 + n]
            p += 4 + n

            header_timing = bool(block) and block[0] == 0
            is_header = len(block) == 19 and header_timing
            pilot_pulses = HEADER_PILOT_PULSES if header_timing else DATA_PILOT_PULSES
            states = (pilot_pulses * PILOT_LENGTH + SYNC1_LENGTH + SYNC2_LENGTH
                      + bits_time(block, ZERO_LENGTH, ONE_LENGTH, 8))

            x = 0
            for b in block:
                x ^= b
            if block and x:
                out["checksum_errors"] += 1

            if is_header:
                kind = block[1]
                length, param, _param2 = struct.unpack_from("<HHH", block, 12)
                out["files"].append({
                    "type": KINDS[kind] if kind < len(KINDS) else "type_%d" % kind,
                    "name": block[2:12].decode("latin-1").rstrip(),
                    "length": length,
                    "param": param,
                })

        elif bid == 0x11:  # Turbo speed data
            (pilot_length, sync1, sync2, zero, one,
             pilot_pulses) = struct.unpack_from("<HHHHHH", data, p)
            used = data[p + 12]
            pause_ms = struct.unpack_from("<H", data, p + 13)[0]
            n = u24(data, p + 15)
            block = data[p + 18:p + 18 + n]
            p += 18 + n

            states = (pilot_pulses * pilot_length + sync1 + sync2
                      + bits_time(block, zero, one, used))

        elif bid == 0x12:  # Pure tone
            pulse_length, pulses = struct.unpack_from("<HH", data, p)
            p += 4
            states = pulse_length * pulses

        elif bid == 0x13:  # Pulse sequence
            count = data[p]
            p += 1
            pulses = struct.unpack_from("<%dH" % count, data, p) if count else ()
            p += 2 * count
            states = sum(pulses)

        elif bid == 0x14:  # Pure data
            zero, one = struct.unpack_from("<HH", data, p)
            used = data[p + 4]
            pause_ms = struct.unpack_from("<H", data, p + 5)[0]
            n = u24(data, p + 7)
            block = data[p + 10:p + 10 + n]
            p += 10 + n
            states = bits_time(block, zero, one, used)

        elif bid == 0x15:  # Direct recording
            sample_states, pause_ms = struct.unpack_from("<HH", data, p)
            used = data[p + 4]
            n = u24(data, p + 5)
            p += 8 + n
            sample_count = ((n - 1) * 8 + used) if n else 0
            states = sample_states * sample_count

        elif bid == 0x18:  # CSW recording: its first field gives the block length.
            n = u32(data, p)
            p += 4 + n

        elif bid == 0x19:  # Generalized data: its first field gives the block length.
            n = u32(data, p)
            p += 4 + n

        elif bid == 0x20:  # Pause, or stop-the-tape when the duration is zero.
            pause_ms, = struct.unpack_from("<H", data, p)
            p += 2
            if pause_ms == 0:
                out["stops"] += 1

        elif bid == 0x21:  # Group start
            n = data[p]
            p += 1 + n

        elif bid == 0x22:  # Group end
            pass

        elif bid == 0x23:  # Jump to block; playback does not follow jumps.
            p += 2

        elif bid == 0x24:  # Loop start
            repetitions, = struct.unpack_from("<H", data, p)
            p += 2
            loop_stack.append(repetitions)
            if repetitions:
                loop_multiplier *= repetitions

        elif bid == 0x25:  # Loop end
            if loop_stack:
                repetitions = loop_stack.pop()
                if repetitions:
                    loop_multiplier //= repetitions

        elif bid == 0x26:  # Call sequence
            count, = struct.unpack_from("<H", data, p)
            p += 2 + 2 * count

        elif bid == 0x27:  # Return from call
            pass

        elif bid == 0x28:  # Select block
            n, = struct.unpack_from("<H", data, p)
            p += 2 + n

        elif bid == 0x2A:  # Stop the tape in 48K mode
            out["stops"] += 1
            p = _skip_stop_48k_suffix(data, p)

        elif bid == 0x2B:  # Set signal level
            # The usual 1.20 form is one BYTE.  Also accept a DWORD-length-prefixed form.
            if p + 5 <= len(data) and u32(data, p) == 1:
                p += 5
            else:
                p += 1

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
                key_id, text_len = body[q], body[q + 1]
                info[INFO.get(key_id, "info_%02x" % key_id)] = \
                    body[q + 2:q + 2 + text_len].decode("latin-1")
                q += 2 + text_len
            out["archive"] = info

        elif bid == 0x33:  # Hardware type
            count = data[p]
            p += 1 + 3 * count

        elif bid == 0x34:  # Emulation info
            p += 8

        elif bid == 0x35:  # Custom info
            n = u32(data, p + 16)
            p += 20 + n

        elif bid == 0x40:  # Snapshot block
            n = u32(data, p + 1)
            p += 5 + n

        elif bid == 0x5A:  # Glue block: ID is 'Z', followed by 'XTape!\x1a' and version.
            p += 9

        else:
            raise TzxError("unknown block 0x%02X at %d" % (bid, p - 1))

        out["blocks"] += 1
        key = "0x%02X" % bid
        out["block_types"][key] = out["block_types"].get(key, 0) + 1

        total_states += states * loop_multiplier
        total_pause_ms += pause_ms * loop_multiplier

    duration = Fraction(total_states, CLOCK) + Fraction(total_pause_ms, 1000)
    out["duration"] = str(duration)
    return out
