"""ScreamTracker 3 modules (.s3m): header, order list, instruments, patterns, play time."""
import struct
from fractions import Fraction


class S3mError(ValueError):
    pass


def _instrument(data, para):
    """Sample header of the type-1 instrument at paragraph ``para``.

    Returns ``None`` for empty slots and non-sampled (AdLib) instruments.
    Sample lengths are in frames; loop begin and end are absolute frame
    positions, so one loop pass is ``end - begin`` frames.
    """
    p = para * 16
    typ = data[p]
    if typ != 1:                               # empty slot or AdLib instrument
        return None
    hi = data[p + 13]
    lo, = struct.unpack_from("<H", data, p + 14)
    length, lbeg, lend = struct.unpack_from("<III", data, p + 16)
    vol, _, pack, flags = data[p + 28], data[p + 29], data[p + 30], data[p + 31]
    c2spd, = struct.unpack_from("<I", data, p + 32)
    name = data[p + 48:p + 76].split(b"\0")[0].decode("latin-1").rstrip()
    loop = bool(flags & 1)
    return {"name": name, "frames": length,
            "bits": 16 if flags & 4 else 8, "stereo": bool(flags & 2),
            "loop": loop, "loop_start": lbeg if loop else 0,
            "loop_length": max(0, lend - lbeg) if loop else 0,
            "volume": vol, "c2spd": c2spd,
            "data_offset": ((hi << 16) | lo) * 16}


def _packed_rows(data, start, end):
    """Parse up to 64 packed pattern rows from ``data[start:end]``.

    A row is a series of packed cells terminated by a zero byte.  Returns
    ``(rows, truncated)``; ``truncated`` is true when the data ran out in the
    middle of a row, which tells the caller that ``end`` was too small.
    """
    p, rows = start, []
    for _ in range(64):
        row = {}
        if p >= end:                           # no data left: rest is empty
            rows.append(row)
            continue
        while p < end:
            what = data[p]; p += 1
            if what == 0:                      # end of row
                break
            need = (2 if what & 32 else 0) + (1 if what & 64 else 0) + (2 if what & 128 else 0)
            if p + need > end:                 # row cut off by the bound
                return rows, True
            note = ins = vol = cmd = info = None
            if what & 32:
                note, ins = data[p], data[p + 1]; p += 2
            if what & 64:
                vol = data[p]; p += 1
            if what & 128:
                cmd, info = data[p], data[p + 1]; p += 2
            row[what & 31] = (note, ins, vol, cmd, info)
        else:
            return rows, True                  # row has no terminating zero
        rows.append(row)
    return rows, False


def _pattern(data, para):
    """64 rows of {channel: (note, ins, vol, cmd, info)}.

    ``para`` is the pattern's paragraph (16 byte) offset; zero means an empty
    pattern.  The length word in the pattern header bounds the packed data;
    trackers disagree on whether it counts its own two bytes, so both readings
    are tried before falling back to the end of the file.
    """
    empty = [{} for _ in range(64)]
    if not para or para * 16 + 2 > len(data):
        return empty
    base = para * 16
    start = base + 2
    declared, = struct.unpack_from("<H", data, base)
    rows = empty
    for end in (base + declared, base + declared + 2, len(data)):
        end = min(end, len(data))
        if end < start:
            continue
        rows, truncated = _packed_rows(data, start, end)
        if not truncated:
            break
    rows = rows + [{} for _ in range(64 - len(rows))]
    return rows[:64]


def _break_row(info):
    """Cxx takes its parameter as two decimal (BCD) digits."""
    row = (info >> 4) * 10 + (info & 0x0F)
    return row if row < 64 else None


def _duration(orders, pats, speed, tempo):
    """Seconds to play the order list once, following ScreamTracker 3 timing.

    ``orders`` is the order list as played (marker entries already removed).
    A row lasts ``speed`` ticks of ``2.5 / tempo`` seconds.  ``Axx`` sets the
    speed, ``Txx`` the tempo, ``Bxx`` jumps to an order position and ``Cxx``
    breaks to row xx (BCD) of the next position.  Playback ends when a row
    would be played a second time or the order list runs out.
    """
    total, seen, pos, row = Fraction(0), set(), 0, 0
    while pos < len(orders):
        if (pos, row) in seen:                 # a row is about to be repeated
            break
        seen.add((pos, row))
        grid = pats[orders[pos]]
        jump = brk = None
        for ch in sorted(grid[row]):
            note, ins, vol, cmd, info = grid[row][ch]
            if cmd == 1 and info:              # Axx set speed
                speed = info
            elif cmd == 20 and info:           # Txx set tempo
                tempo = info
            elif cmd == 2:                     # Bxx jump to order position
                jump = info
            elif cmd == 3:                     # Cxx pattern break
                brk = _break_row(info)
        if speed <= 0 or tempo <= 0:           # malformed timing: stop here
            break
        total += Fraction(speed * 5, tempo * 2)
        if jump is not None:
            pos = jump
            row = brk or 0
        elif brk is not None:
            pos, row = pos + 1, brk
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
    # A channel setting of 0-15 is a left channel and 16-31 a right one; any
    # other value disables it (255 for unused slots, or the 0x80 bit some
    # trackers set on a muted channel).
    channels = sum(1 for c in data[0x40:0x60] if c < 32)
    # 254 is a marker that is skipped, 255 ends the order list.
    orders = []
    for o in data[0x60:0x60 + ordnum]:
        if o == 255:
            break
        if o == 254:
            continue
        if o >= patnum:
            raise S3mError("order list refers to missing pattern %d" % o)
        orders.append(o)
    p = 0x60 + ordnum
    ins_ptr = struct.unpack_from("<%dH" % insnum, data, p)
    pat_ptr = struct.unpack_from("<%dH" % patnum, data, p + 2 * insnum)
    samples = []
    for i, ptr in enumerate(ins_ptr):
        if not ptr:
            continue
        s = _instrument(data, ptr)
        if s is not None:                      # sampled, not AdLib or empty
            s.pop("data_offset")
            samples.append({"index": i + 1, **s})
    pats = [_pattern(data, ptr) for ptr in pat_ptr]
    notes = sum(1 for pt in pats for row in pt for c in row.values() if c[0] is not None and c[0] < 254)
    tracker = {1: "Scream Tracker", 3: "Impulse Tracker", 4: "Schism Tracker"}.get(cwt >> 12, "unknown")
    return {
        "title": title, "tracker": tracker, "tracker_version": "%x.%02x" % ((cwt >> 8) & 0xF, cwt & 0xFF),
        "signed_samples": ffi == 1, "stereo": bool(mvol & 0x80), "channels": channels,
        "orders": orders, "patterns": patnum, "notes": notes, "samples": samples,
        "duration": str(_duration(orders, pats, speed, tempo)),
    }
