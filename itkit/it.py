"""Impulse Tracker modules (.it): header, orders, channels, samples, patterns, song message."""
import struct

# Order list markers.
ORDER_END = 255
ORDER_SEPARATOR = 254

# Note values inside packed pattern data.
NOTE_CUT = 253
NOTE_OFF = 254
NOTE_LAST = 119  # notes run 0..119 (120 is the note fade, not a playable note)

# Sample header flags (IMPS, offset 0x11).
SMP_LOOP_ON = 0x01
SMP_LOOP_PINGPONG = 0x02
SMP_SUSTAIN_ON = 0x04
SMP_SUSTAIN_PINGPONG = 0x08
SMP_STEREO = 0x10
SMP_16BIT = 0x20
SMP_COMPRESSED = 0x40

# Channel pan byte flags (header offsets 0x40..0x7f).
PAN_MUTED = 0x80
PAN_SURROUND = 0x40

# Packed pattern bytes.
MASK_NOTE = 0x01
MASK_INSTRUMENT = 0x02
MASK_VOLPAN = 0x04
MASK_COMMAND = 0x08
MASK_PARAMETER = 0x10
MASK_PARTIAL = 0x40   # set: merge the low bits into the running mask
MASK_CHANNEL = 0x80   # set: channel switch, (byte & 0x7f) - 1


class ItError(ValueError):
    pass


def text(raw):
    return raw.split(b"\0")[0].decode("cp437").rstrip() or None


def version(v):
    # Version words are stored as major in the high byte and two hex digits
    # in the low byte, e.g. 0x0214 -> "2.14".
    return "%d.%02X" % ((v >> 8) & 0xFF, v & 0xFF)


def loop_kind(flags, on, pingpong):
    if not (flags & on):
        return None
    return "pingpong" if (flags & pingpong) else "forward"


def parse_sample(data, off):
    if data[off:off + 4] != b"IMPS":
        raise ItError("bad sample header at %d" % off)
    flags = data[off + 0x11]
    length, _lb, _le, c5, _sb, _se, pointer = struct.unpack_from(
        "<7I", data, off + 0x30)
    bits = 16 if (flags & SMP_16BIT) else 8
    stereo = bool(flags & SMP_STEREO)
    compressed = bool(flags & SMP_COMPRESSED)
    if compressed or pointer == 0:
        data_bytes = None
    else:
        channels = 2 if stereo else 1
        bytes_per_frame = (bits // 8) * channels
        data_bytes = length * bytes_per_frame
    return {
        "name": text(data[off + 0x14:off + 0x2e]),
        "length": length,
        "bits": bits,
        "stereo": stereo,
        "c5_speed": c5,
        "loop": loop_kind(flags, SMP_LOOP_ON, SMP_LOOP_PINGPONG),
        "sustain_loop": loop_kind(flags, SMP_SUSTAIN_ON, SMP_SUSTAIN_PINGPONG),
        "compressed": compressed,
        "data_bytes": data_bytes,
    }


def parse_pattern(data, off):
    """Decode one packed IT pattern.

    Returns (row_count, notes, note_offs, note_cuts).  A missing pattern
    (offset 0) is treated as an empty 64-row pattern.
    """
    if off == 0:
        return 64, 0, 0, 0
    data_len, rows = struct.unpack_from("<HH", data, off)
    rows = rows or 64
    end = min(off + 8 + data_len, len(data))
    pos = off + 8
    notes = note_offs = note_cuts = 0
    last_mask = 0
    channel = 0
    for _ in range(rows):
        if pos >= end:
            break
        marker = data[pos]
        pos += 1
        if marker == 0:
            continue  # empty row
        channel = marker - 1
        while pos < end:
            entry = data[pos]
            pos += 1
            if entry == 0:
                break  # end of row
            if entry & MASK_CHANNEL:
                channel = (entry & 0x7F) - 1
                continue
            if entry & MASK_PARTIAL:
                last_mask |= entry & 0x1F  # add fields to running mask
            else:
                last_mask = entry          # replace running mask
            note = None
            field_count = bin(last_mask & 0x1F).count("1")
            if pos + field_count > end:
                raise ItError("truncated pattern data at %d" % off)
            if last_mask & MASK_NOTE:
                note = data[pos]
                pos += 1
            if last_mask & MASK_INSTRUMENT:
                pos += 1
            if last_mask & MASK_VOLPAN:
                pos += 1
            if last_mask & MASK_COMMAND:
                pos += 1
            if last_mask & MASK_PARAMETER:
                pos += 1
            if channel < 64 and note is not None:
                if note <= NOTE_LAST:
                    notes += 1
                elif note == NOTE_OFF:
                    note_offs += 1
                elif note == NOTE_CUT:
                    note_cuts += 1
    return rows, notes, note_offs, note_cuts


def parse_message(data, msglen, msgoff, special):
    if not (special & 0x01) or msglen == 0 or msgoff == 0 or msgoff >= len(data):
        return None
    raw = data[msgoff:msgoff + msglen].split(b"\0")[0]
    return raw.decode("cp437").replace("\r", "\n").rstrip() or None


def parse(data):
    if len(data) < 192 or data[:4] != b"IMPM":
        raise ItError("not an Impulse Tracker module")
    ordnum, insnum, smpnum, patnum, cwt, cmwt, flags, special = struct.unpack_from("<8H", data, 32)
    gv, mv, speed, tempo, sep = data[48:53]
    msglen, msgoff = struct.unpack_from("<HI", data, 54)
    pans = data[64:128]
    p = 192
    raw_orders = list(data[p:p + ordnum])
    p += ordnum
    p += 4 * insnum  # instrument offsets are not needed for the summary
    smp_off = struct.unpack_from("<%dI" % smpnum, data, p)
    p += 4 * smpnum
    pat_off = struct.unpack_from("<%dI" % patnum, data, p)

    # Play order: drop separator markers and everything from the
    # end-of-song marker onwards.
    played = []
    for order in raw_orders:
        if order == ORDER_END:
            break
        if order == ORDER_SEPARATOR:
            continue
        played.append(order)

    enabled = [v for v in pans if v != 0 and not (v & PAN_MUTED)]
    samples = [parse_sample(data, off) for off in smp_off]

    pattern_rows = []
    notes = []
    note_offs = []
    note_cuts = []
    for off in pat_off:
        rows, n, offs, cuts = parse_pattern(data, off)
        pattern_rows.append(rows)
        notes.append(n)
        note_offs.append(offs)
        note_cuts.append(cuts)

    return {
        "title": text(data[4:30]),
        "tracker": version(cwt),
        "compatible": version(cmwt),
        "stereo": bool(flags & 1),
        "instrument_mode": bool(flags & 4),
        "linear_slides": bool(flags & 8),
        "global_volume": gv,
        "mix_volume": mv,
        "speed": speed,
        "tempo": tempo,
        "orders": played,
        "channels": len(enabled),
        "surround_channels": sum(1 for v in enabled if v & PAN_SURROUND),
        "instruments": insnum,
        "samples": samples,
        "pattern_rows": pattern_rows,
        "notes": notes,
        "note_offs": note_offs,
        "note_cuts": note_cuts,
        "message": parse_message(data, msglen, msgoff, special),
    }
