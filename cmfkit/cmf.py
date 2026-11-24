"""Creative Music Files (.cmf): header v1.0 / v1.1, strings, instruments,
MIDI-style event stream (variable-length deltas, running status, meta and
system exclusive events) and the CMF-specific controllers.

CMF v1.0 has no instrument-count/tempo header words: the number of
instruments is derived from the instrument table (16 bytes each).  Its
header is 0x24 bytes; v1.1 adds two words at 0x24 (instrument count, tempo).
"""
import struct
from fractions import Fraction

# CMF controllers (Creative extensions beyond the standard MIDI controllers).
CMF_MARKER = 0x63          # 99: marker
CMF_RHYTHM_MODE = 0x66     # 102: rhythm mode on/off
CMF_TRANSPOSE = 0x67       # 103: transpose, signed byte in semitones

# MIDI channel message kinds, with the number of data bytes that follow.
CHANNEL_DATA_LEN = {
    0x80: 2,  # note off
    0x90: 2,  # note on
    0xA0: 2,  # polyphonic key pressure
    0xB0: 2,  # control change
    0xE0: 2,  # pitch wheel change
    0xC0: 1,  # program change
    0xD0: 1,  # channel pressure
}


class CmfError(ValueError):
    pass


def cstr(data, off):
    """Read a NUL-terminated string at a header offset; None when unused."""
    if not off or off >= len(data):
        return None
    end = data.find(b"\0", off)
    return data[off:end if end >= 0 else len(data)].decode("latin-1") or None


def _read_vlq(data, p):
    """MIDI variable-length quantity. Returns (value, new_offset)."""
    value = 0
    for _ in range(4):
        if p >= len(data):
            raise CmfError("truncated variable-length value")
        byte = data[p]
        p += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, p
    raise CmfError("variable-length value too long")


def parse(data):
    if data[:4] != b"CTMF" or len(data) < 0x24:
        raise CmfError("not a Creative Music File")

    minor, major = data[4], data[5]
    ins_off, mus_off, tpq, tps, title_off, composer_off, remarks_off = \
        struct.unpack_from("<7H", data, 6)

    if mus_off >= len(data):
        raise CmfError("music block offset outside file")

    # Channel-in-use table at 0x14..0x23.
    used = [i for i in range(16) if data[0x14 + i]]

    # v1.0 has no count/tempo words; count the 16-byte instrument records.
    if (major, minor) == (1, 0):
        if mus_off > ins_off:
            ins_count = (mus_off - ins_off) // 16
        else:
            ins_count = 0
        tempo = None
    else:
        if len(data) < 0x28:
            raise CmfError("truncated CMF v1.1 header")
        ins_count, tempo = struct.unpack_from("<HH", data, 0x24)

    title = cstr(data, title_off)
    composer = cstr(data, composer_off)
    remarks = cstr(data, remarks_off)

    ticks = 0
    notes = 0
    markers = 0
    rhythm_on = 0
    transposes = []
    running_status = None
    ended = False

    p = mus_off
    n = len(data)
    first_event = True
    while p < n:
        # Delta time: MIDI variable-length quantity.  Some Sound Blaster tools
        # omit the leading zero delta of the very first event.
        if first_event and data[p] & 0x80:
            delta = 0
        else:
            delta, p = _read_vlq(data, p)
        first_event = False
        ticks += delta

        # Status byte; running status reuses the previous channel status.
        if p >= n:
            raise CmfError("truncated event: missing status byte")
        if data[p] & 0x80:
            status = data[p]
            p += 1
        else:
            if running_status is None:
                raise CmfError("event without status byte at %d" % p)
            status = running_status

        if status == 0xFF:
            # Meta event: type, VLQ length, payload.
            if p >= n:
                raise CmfError("truncated meta event")
            meta = data[p]
            length, p = _read_vlq(data, p + 1)
            if p + length > n:
                raise CmfError("truncated meta event payload")
            p += length
            if meta == 0x2F:
                ended = True
                break
            continue

        if status in (0xF0, 0xF7):
            # System exclusive (or escape) event: VLQ length, payload.
            length, p = _read_vlq(data, p)
            if p + length > n:
                raise CmfError("truncated system exclusive event")
            p += length
            continue

        kind = status & 0xF0
        if kind not in CHANNEL_DATA_LEN:
            raise CmfError("unknown status 0x%02X at %d" % (status, p))
        running_status = status

        data_len = CHANNEL_DATA_LEN[kind]
        if p + data_len > n:
            raise CmfError("truncated channel event")
        a = data[p]
        v = data[p + 1] if data_len == 2 else 0
        p += data_len

        if kind == 0x90:
            # A note-on with velocity 0 is a note-off, not a new note.
            if v:
                notes += 1
        elif kind == 0xB0:
            if a == CMF_MARKER:
                markers += 1
            elif a == CMF_RHYTHM_MODE and v:
                rhythm_on += 1
            elif a == CMF_TRANSPOSE:
                transposes.append(v - 256 if v >= 0x80 else v)

    if tps:
        duration = str(Fraction(ticks, tps))
    else:
        duration = None

    return {
        "version": "%d.%d" % (major, minor),
        "title": title,
        "composer": composer,
        "remarks": remarks,
        "instruments": ins_count,
        "tempo": tempo,
        "channels_used": used,
        "ticks_per_quarter": tpq,
        "ticks_per_second": tps,
        "ticks": ticks,
        "duration": duration,
        "notes": notes,
        "markers": markers,
        "rhythm_mode_on": rhythm_on,
        "transpose": transposes,
        "end_of_track": ended,
    }
