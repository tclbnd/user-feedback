"""ScreamTracker 3 modules (.s3m): header, order list, instruments, patterns, play time."""
import struct
from fractions import Fraction


class S3mError(ValueError):
    pass


def _instrument(data, para):
    p = para * 16
    typ = data[p]
    hi = data[p + 13]
    lo, = struct.unpack_from("<H", data, p + 14)
    length, lbeg, lend = struct.unpack_from("<III", data, p + 16)
    vol, _, pack, flags = data[p + 28], data[p + 29], data[p + 30], data[p + 31]
    c2spd, = struct.unpack_from("<I", data, p + 32)
    name = data[p + 48:p + 76].split(b"\0")[0].decode("latin-1").rstrip()
    loop = bool(flags & 1)
    return {"name": name, "frames": length, "bits": 8, "stereo": False,
            "loop": loop, "loop_start": lbeg if loop else 0, "loop_length": lend if loop else 0,
            "volume": vol, "c2spd": c2spd, "data_offset": ((hi << 16) | lo) * 16}


def _pattern(data, para):
    """64 rows of {channel: (note, ins, vol, cmd, info)}."""
    p = para + 2
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


def _duration(orders, pats, speed, tempo):
    total, seen, pos, row = Fraction(0), set(), 0, 0
    while pos < len(orders):
        pi = orders[pos]
        grid = pats[pi] if pi < len(pats) else [{}] * 64
        if (pos, row) in seen:
            break
        seen.add((pos, row))
        jump = brk = None
        for ch in sorted(grid[row]):
            note, ins, vol, cmd, info = grid[row][ch]
            if cmd == 1:                       # Axx set speed
                speed = info
            elif cmd == 20:                    # Txx set tempo
                tempo = info
            elif cmd == 3:                     # Cxx pattern break
                brk = info
        total += Fraction(speed * 5, tempo * 2)
        if brk is not None:
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
    channels = sum(1 for c in data[0x40:0x60] if c != 255)
    orders = list(data[0x60:0x60 + ordnum])
    for o in orders:
        if o >= patnum:
            raise S3mError("order list refers to missing pattern %d" % o)
    p = 0x60 + ordnum
    ins_ptr = struct.unpack_from("<%dH" % insnum, data, p)
    pat_ptr = struct.unpack_from("<%dH" % patnum, data, p + 2 * insnum)
    samples = []
    for i, ptr in enumerate(ins_ptr):
        s = _instrument(data, ptr) if ptr else None
        if s:
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
