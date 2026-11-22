"""ScreamTracker 3 modules (.s3m): header, order list, instruments, patterns, play time."""
import struct
from fractions import Fraction

# Order list marker values (everything else is a pattern number 0..253).
ORDER_END = 255        # end of song: playback stops here
ORDER_SEPARATOR = 254  # visual separator only: skipped during playback
# Pattern cell note byte values.
NOTE_NONE = 0          # no note field in the cell
NOTE_CUT = 254         # note cut (^^)
NOTE_OFF = 255         # note off (~~)
# Instrument sample flags (byte at header offset 31).
SAMPLE_LOOP = 1        # the sample loops between loop start and loop end
SAMPLE_STEREO = 2      # 16-bit/8-bit interleaved stereo sample
SAMPLE_16BIT = 4       # 16-bit sample instead of 8-bit
# Channel setting bytes (32 entries at header offset 0x40).
CHANNEL_DISABLED = 255  # unused channel slot
CHANNEL_MUTED = 0x20    # bit 5: channel switched off / muted


class S3mError(ValueError):
    pass


def _instrument(data, para):
    p = para * 16
    typ = data[p]
    hi = data[p + 13]
    lo, = struct.unpack_from("<H", data, p + 14)
    # Length, loop begin and loop end are frame positions for every sample width.
    length, lbeg, lend = struct.unpack_from("<III", data, p + 16)
    vol, _, pack, flags = data[p + 28], data[p + 29], data[p + 30], data[p + 31]
    c2spd, = struct.unpack_from("<I", data, p + 32)
    name = data[p + 48:p + 76].split(b"\0")[0].decode("latin-1").rstrip()
    loop = bool(flags & SAMPLE_LOOP)
    return {"type": typ, "name": name, "frames": length,
            "bits": 16 if flags & SAMPLE_16BIT else 8,
            "stereo": bool(flags & SAMPLE_STEREO),
            "loop": loop, "loop_start": lbeg if loop else 0,
            "loop_length": (lend - lbeg) if loop else 0,
            "volume": vol, "c2spd": c2spd,
            "data_offset": ((hi << 16) | lo) * 16}


def _pattern(data, para):
    """64 rows of {channel: (note, ins, vol, cmd, info)}.

    Pattern pointers are paragraph addresses (16-byte units); a null pointer
    means the pattern is empty.
    """
    if not para:
        return [{} for _ in range(64)]
    p = para * 16 + 2
    rows = []
    for _ in range(64):
        row = {}
        while True:
            what = data[p]; p += 1
            if what == 0:
                break
            ch = what & 31
            note = ins = vol = cmd = info = None
            if what & 32:
                note, ins = data[p], data[p + 1]; p += 2
            if what & 64:
                vol = data[p]; p += 1
            if what & 128:
                cmd, info = data[p], data[p + 1]; p += 2
            row[ch] = (note, ins, vol, cmd, info)
        rows.append(row)
    return rows


def _bcd_row(info):
    """Cxx pattern break parameter: two decimal digits (e.g. 0x12 -> row 12)."""
    return (info >> 4) * 10 + (info & 0x0F)


def _duration(orders, pats, speed, tempo):
    """Play the filtered order list once, ScreamTracker 3 style.

    A row lasts ``speed`` ticks; at 125..255 tempo a tick is 2.5/tempo seconds,
    hence each row takes speed*5/(tempo*2) seconds. Axx changes the speed, Txx
    the tempo, Bxx jumps to an order position and Cxx breaks to the given
    (decimal) row of the next position. Playback stops when a row would be
    played a second time or when the order list runs out.
    """
    total, seen, pos, row = Fraction(0), set(), 0, 0
    while pos < len(orders):
        if (pos, row) in seen:
            break
        seen.add((pos, row))
        pi = orders[pos]
        grid = pats[pi] if pi < len(pats) else [{}] * 64
        jump = brk = None
        for ch in sorted(grid[row]):
            note, ins, vol, cmd, info = grid[row][ch]
            if cmd == 1:                       # Axx set speed
                speed = info
            elif cmd == 20:                    # Txx set tempo
                tempo = info
            elif cmd == 2:                     # Bxx position jump
                jump = info
            elif cmd == 3:                     # Cxx pattern break
                brk = _bcd_row(info)
        total += Fraction(speed * 5, tempo * 2)
        if jump is not None:
            # Bxx wins over Cxx when both appear on the same row.
            pos, row = jump, 0
        elif brk is not None:
            pos, row = pos + 1, min(brk, 63)
        else:
            row += 1
            if row == 64:
                pos, row = pos + 1, 0
    return total


def parse(data):
    if len(data) < 0x60 or data[0x2C:0x30] != b"SCRM":
        raise S3mError("not a ScreamTracker 3 module")
    title = data[:28].split(b"\0")[0].decode("latin-1").rstrip()
    ordnum, insnum, patnum, flags, cwt, ffi = struct.unpack_from("<6H", data, 0x20)
    gvol, speed, tempo, mvol = data[0x30], data[0x31], data[0x32], data[0x33]
    # 255 marks an unused slot; bit 5 (0x20) marks a channel switched off.
    channels = sum(1 for c in data[0x40:0x60]
                   if c != CHANNEL_DISABLED and not (c & CHANNEL_MUTED))
    # The order list ends at the first 255; 254 entries are separators that
    # never play a pattern.
    raw_orders = list(data[0x60:0x60 + ordnum])
    orders = []
    for o in raw_orders:
        if o == ORDER_END:
            break
        if o == ORDER_SEPARATOR:
            continue
        if o >= patnum:
            raise S3mError("order list refers to missing pattern %d" % o)
        orders.append(o)
    p = 0x60 + ordnum
    ins_ptr = struct.unpack_from("<%dH" % insnum, data, p)
    pat_ptr = struct.unpack_from("<%dH" % patnum, data, p + 2 * insnum)
    samples = []
    for i, ptr in enumerate(ins_ptr):
        s = _instrument(data, ptr) if ptr else None
        # Only sampled instruments (type 1); type 2 is an OPL/AdLib melody.
        if s and s.pop("type") == 1:
            s.pop("data_offset")
            samples.append({"index": i + 1, **s})
    pats = [_pattern(data, ptr) for ptr in pat_ptr]
    # Only real notes (1..253) count: empty cells, note cut (254) and
    # note off (255) do not play a note.
    notes = sum(1 for pt in pats for row in pt for c in row.values()
                if c[0] is not None and c[0] not in (NOTE_NONE, NOTE_CUT, NOTE_OFF))
    tracker = {1: "Scream Tracker", 3: "Impulse Tracker", 4: "Schism Tracker"}.get(cwt >> 12, "unknown")
    return {
        "title": title, "tracker": tracker, "tracker_version": "%x.%02x" % ((cwt >> 8) & 0xF, cwt & 0xFF),
        "signed_samples": ffi == 1, "stereo": bool(mvol & 0x80), "channels": channels,
        "orders": orders, "patterns": patnum, "notes": notes, "samples": samples,
        "duration": str(_duration(orders, pats, speed, tempo)),
    }
