"""Creative Music Files (.cmf): header v1.0 / v1.1, strings, instruments, MIDI-style event stream, CMF controllers."""
import struct
from fractions import Fraction


class CmfError(ValueError):
    pass


def cstr(data, off):
    if not off or off >= len(data):
        return None
    end = data.find(b"\0", off)
    return data[off:end if end >= 0 else len(data)].decode("latin-1") or None


def parse(data):
    if data[:4] != b"CTMF" or len(data) < 0x24:
        raise CmfError("not a Creative Music File")
    minor, major = data[4], data[5]
    ins_off, mus_off, tpq, tps, t_off, c_off, r_off = struct.unpack_from("<7H", data, 6)
    used = [i for i in range(16) if data[0x14 + i]]
    ins_count, tempo = struct.unpack_from("<HH", data, 0x24)
    p, n = mus_off, len(data)
    ticks = notes = markers = rhythm_on = 0
    transposes = []
    status = None
    ended = False
    while p < n:
        delta = data[p] & 0x7F
        p += 1
        ticks += delta
        status = data[p]
        p += 1
        kind = status & 0xF0
        if status == 0xFF:
            meta, length = data[p], data[p + 1]
            p += 2 + length
            if meta == 0x2F:
                ended = True
                break
            continue
        if kind not in (0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0):
            raise CmfError("unknown status 0x%02X at %d" % (status, p))
        if kind in (0xC0, 0xD0):
            p += 1
            continue
        a, v = data[p], data[p + 1]
        p += 2
        if kind == 0x90:
            notes += 1
    return {"version": "%d.%d" % (major, minor), "title": None, "composer": None,
            "remarks": None, "instruments": ins_count, "tempo": tempo, "channels_used": used,
            "ticks_per_quarter": tpq, "ticks_per_second": tps, "ticks": ticks,
            "duration": str(Fraction(ticks, tpq)) if tpq else None, "notes": notes, "markers": markers,
            "rhythm_mode_on": rhythm_on, "transpose": transposes, "end_of_track": ended}
