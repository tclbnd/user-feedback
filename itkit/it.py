"""Impulse Tracker modules (.it): header, orders, channels, samples, patterns, song message."""
import struct


class ItError(ValueError):
    pass


# Sample flag bits (IMPS header, Flg).
SMP_16BIT = 0x04
SMP_STEREO = 0x02
SMP_COMPRESSED = 0x08
SMP_LOOP = 0x10
SMP_SUSTAIN = 0x20
SMP_PINGPONG = 0x40
SMP_KNOWN_FLAGS = 0x7f

# IT pattern channel mask bits.
MASK_NOTE = 0x01
MASK_INSTRUMENT = 0x02
MASK_VOLPAN = 0x04
MASK_COMMAND = 0x08
MASK_LAST = 0x80

# Note values in pattern data.
NOTE_OFF = 253
NOTE_CUT = 254
NOTE_LAST = 119  # notes are 0..119


def text(raw):
    return raw.split(b"\0")[0].decode("cp437").rstrip() or None


def version(v):
    """The version word is a major byte and a two-digit hexadecimal minor, e.g. 0x0214 -> '2.14'."""
    return "%d.%02x" % (v >> 8, v & 0xff)


def song_message(data, length, offset):
    if not length or not offset:
        return None
    raw = data[offset:offset + length]
    msg = raw.split(b"\0")[0].decode("cp437")
    msg = msg.replace("\r\n", "\n").replace("\r", "\n")
    return msg or None


# Field offsets inside an IMPS header.  Impulse Tracker 2.00 used a 64-byte
# header; 2.04 and later use an 80-byte header with the numeric fields moved.
_SAMPLE_LAYOUTS = (
    # (gvl, flg, length, loop_begin, loop_end, c5_speed,
    #  sustain_begin, sustain_end, data_pointer)
    {"gvl": 46, "flg": 31, "length": 48, "loop_begin": 52, "loop_end": 56,
     "c5": 60, "sustain_begin": 64, "sustain_end": 68, "pointer": 76},
    {"gvl": 30, "flg": 31, "length": 40, "loop_begin": 44, "loop_end": 48,
     "c5": 56, "sustain_begin": 60, "sustain_end": 64, "pointer": 76},
)


def _layout_score(data, off, layout):
    flg = data[off + layout["flg"]]
    c5 = struct.unpack_from("<I", data, off + layout["c5"])[0]
    gvl = data[off + layout["gvl"]]
    score = 0
    if not (flg & ~SMP_KNOWN_FLAGS):
        score += 2
    if 1000 <= c5 <= 200000:
        score += 2
    if gvl <= 128:
        score += 1
    return score


def _loop_kind(flags, enable_bit):
    if not (flags & enable_bit):
        return None
    return "pingpong" if (flags & SMP_PINGPONG) else "forward"


def parse_sample(data, off):
    if data[off:off + 4] != b"IMPS":
        raise ItError("bad sample header at %d" % off)
    layouts = sorted(_SAMPLE_LAYOUTS,
                     key=lambda layout: _layout_score(data, off, layout),
                     reverse=True)
    layout = layouts[0]
    flags = data[off + layout["flg"]]
    length = struct.unpack_from("<I", data, off + layout["length"])[0]
    c5 = struct.unpack_from("<I", data, off + layout["c5"])[0]
    pointer = struct.unpack_from("<I", data, off + layout["pointer"])[0]
    compressed = bool(flags & SMP_COMPRESSED)
    stereo = bool(flags & SMP_STEREO)
    bits = 16 if (flags & SMP_16BIT) else 8
    if compressed or not pointer or not length:
        data_bytes = None
    else:
        channels = 2 if stereo else 1
        data_bytes = length * channels * (bits // 8)
    return {
        "name": text(data[off + 20:off + 46]),
        "length": length,
        "bits": bits,
        "stereo": stereo,
        "c5_speed": c5,
        "loop": _loop_kind(flags, SMP_LOOP),
        "sustain_loop": _loop_kind(flags, SMP_SUSTAIN),
        "compressed": compressed,
        "data_bytes": data_bytes,
    }


def parse_pattern(data, off):
    """Return (rows, notes, note_offs, note_cuts) for one packed IT pattern."""
    if off == 0:
        # An empty pattern slot plays the default 64 rows and holds no events.
        return 64, 0, 0, 0
    if data[off:off + 4] == b"IMPP":
        # "IMPP", then packed length (word), rows (word), reserved (dword).
        packed_size, rows = struct.unpack_from("<HH", data, off + 4)
        data_start = off + 12
    else:
        # Some tools write patterns without the "IMPP" marker:
        # packed length and row count, each a word, followed by the data.
        packed_size, rows = struct.unpack_from("<HH", data, off)
        data_start = off + 4
    if rows == 0:
        rows = 64  # players treat a zero-row pattern as 64 rows
    end = min(data_start + packed_size, len(data))
    if end < data_start:
        raise ItError("pattern at %d runs past the end of the file" % off)
    p = data_start
    notes = note_offs = note_cuts = 0
    for _ in range(rows):
        # Rows with no events, including any trailing ones, carry no bytes:
        # the packed data simply ends before the row begins.
        while p < end:
            mask = data[p]
            p += 1
            if mask & MASK_LAST:
                # Single-byte mask; bit 7 set also ends the row.
                channel_mask = mask & 0x7f
                last = True
            else:
                # Two-byte mask: the next byte holds mask bits 8..14 and,
                # in bit 7, the end-of-row marker.
                if p >= end:
                    raise ItError("truncated pattern mask at %d" % off)
                high = data[p]
                p += 1
                channel_mask = mask | ((high & 0x7f) << 8)
                last = bool(high & MASK_LAST)

            def need(n):
                if p + n > end:
                    raise ItError("truncated pattern data at %d" % off)

            if channel_mask & MASK_NOTE:
                need(1)
                note = data[p]
                p += 1
                if note <= NOTE_LAST:
                    notes += 1
                elif note == NOTE_OFF:
                    note_offs += 1
                elif note == NOTE_CUT:
                    note_cuts += 1
                # 255 (note fade) and other special values are not counted.
            if channel_mask & MASK_INSTRUMENT:
                need(1)
                p += 1
            if channel_mask & MASK_VOLPAN:
                need(1)
                p += 1
            if channel_mask & MASK_COMMAND:
                need(2)
                p += 2
            if last:
                break
    return rows, notes, note_offs, note_cuts


def parse(data):
    if len(data) < 192 or data[:4] != b"IMPM":
        raise ItError("not an Impulse Tracker module")
    ordnum, insnum, smpnum, patnum, cwt, cmwt, flags, special = struct.unpack_from("<8H", data, 32)
    gv, mv, speed, tempo, sep = data[48:53]
    msglen, msgoff = struct.unpack_from("<HI", data, 54)
    pans = data[64:128]
    p = 192
    orders = list(data[p:p + ordnum])
    p += ordnum
    p += 4 * insnum  # instrument offsets (instruments themselves are not parsed)
    smp_off = struct.unpack_from("<%dI" % smpnum, data, p); p += 4 * smpnum
    pat_off = struct.unpack_from("<%dI" % patnum, data, p)

    # The order list stops at the end-of-song marker (255); the separator
    # marker (254) only divides sections and is never played.
    played = []
    for order in orders:
        if order == 255:
            break
        if order != 254:
            played.append(order)

    enabled = [v for v in pans if v != 0]
    samples = [parse_sample(data, off) for off in smp_off]

    pattern_rows = []
    notes = note_offs = note_cuts = 0
    for off in pat_off:
        rows, n, offs, cuts = parse_pattern(data, off)
        pattern_rows.append(rows)
        notes += n
        note_offs += offs
        note_cuts += cuts

    return {"title": text(data[4:30]), "tracker": version(cwt), "compatible": version(cmwt),
            "stereo": bool(flags & 1), "instrument_mode": bool(flags & 4), "linear_slides": bool(flags & 8),
            "global_volume": gv, "mix_volume": mv, "speed": speed, "tempo": tempo,
            "orders": played,
            "channels": len(enabled),
            "surround_channels": sum(1 for v in enabled if v & 0x20),
            "instruments": insnum,
            "samples": samples, "pattern_rows": pattern_rows,
            "notes": notes, "note_offs": note_offs, "note_cuts": note_cuts,
            "message": song_message(data, msglen, msgoff)}
