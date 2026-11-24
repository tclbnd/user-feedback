"""Creative Music Files (.cmf): header v1.0 / v1.1, strings, instruments, MIDI-style event stream, CMF controllers."""
import struct
from fractions import Fraction


class CmfError(ValueError):
    pass


def cstr(data, off, what="string"):
    if not off:
        return None
    if off >= len(data):
        raise CmfError("%s offset 0x%X is past the end of the file" % (what, off))
    end = data.find(b"\0", off)
    return data[off:end if end >= 0 else len(data)].decode("latin-1") or None


def varlen(data, p):
    """Read a MIDI-style variable length value starting at p."""
    value = 0
    while True:
        if p >= len(data):
            raise CmfError("truncated delta time")
        byte = data[p]
        p += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, p


def parse(data):
    if data[:4] != b"CTMF":
        raise CmfError("not a Creative Music File")
    if len(data) < 0x25:
        raise CmfError("truncated header")
    minor, major = data[4], data[5]
    ins_off, mus_off, tpq, tps, t_off, c_off, r_off = struct.unpack_from("<7H", data, 6)
    used = [i for i in range(16) if data[0x14 + i]]
    if (major, minor) >= (1, 1):
        if len(data) < 0x28:
            raise CmfError("truncated header")
        ins_count, tempo = struct.unpack_from("<HH", data, 0x24)
    else:
        ins_count, tempo = data[0x24], None
    title = cstr(data, t_off, "title")
    composer = cstr(data, c_off, "composer")
    remarks = cstr(data, r_off, "remarks")
    if not mus_off or mus_off >= len(data):
        raise CmfError("no music data")
    p, n = mus_off, len(data)
    ticks = notes = markers = rhythm_on = 0
    transposes = []
    status = None
    ended = False
    while p < n:
        delta, p = varlen(data, p)
        ticks += delta
        if p >= n:
            raise CmfError("truncated event")
        byte = data[p]
        if byte & 0x80:
            status = byte
            p += 1
        elif status is None:
            raise CmfError("data byte 0x%02X at %d without running status" % (byte, p))
        if status == 0xFF:
            if p + 2 > n:
                raise CmfError("truncated meta event")
            meta, length = data[p], data[p + 1]
            p += 2 + length
            if p > n:
                raise CmfError("truncated meta event")
            status = None
            if meta == 0x2F:
                ended = True
                break
            continue
        if status in (0xF0, 0xF7):
            if p >= n:
                raise CmfError("truncated system exclusive event")
            p += 1 + data[p]
            if p > n:
                raise CmfError("truncated system exclusive event")
            status = None
            continue
        kind = status & 0xF0
        if kind not in (0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0):
            raise CmfError("unknown status 0x%02X at %d" % (status, p - 1))
        if kind in (0xC0, 0xD0):
            p += 1
            if p > n:
                raise CmfError("truncated event")
            continue
        if p + 2 > n:
            raise CmfError("truncated event")
        a, v = data[p], data[p + 1]
        p += 2
        if kind == 0x90:
            if v:
                notes += 1
        elif kind == 0xB0:
            if a == 0x66:
                markers += 1
            elif a == 0x67:
                if v:
                    rhythm_on += 1
            elif a == 0x68:
                transposes.append(v)
            elif a == 0x69:
                transposes.append(-v)
    return {"version": "%d.%d" % (major, minor), "title": title, "composer": composer,
            "remarks": remarks, "instruments": ins_count, "tempo": tempo, "channels_used": used,
            "ticks_per_quarter": tpq, "ticks_per_second": tps, "ticks": ticks,
            "duration": str(Fraction(ticks, tps)) if tps else None, "notes": notes, "markers": markers,
            "rhythm_mode_on": rhythm_on, "transpose": transposes, "end_of_track": ended}
