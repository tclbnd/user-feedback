"""Standard MIDI File chunks and track events."""
from collections import namedtuple

Event = namedtuple("Event", "tick kind data")


class MidiError(ValueError):
    pass


def header(data):
    if data[:4] != b"MThd":
        raise MidiError("not a MIDI file")
    length = int.from_bytes(data[4:8], "big")
    fmt, ntrks, division = (int.from_bytes(data[8 + i:10 + i], "big") for i in (0, 2, 4))
    return fmt, ntrks, division, 8 + length


def vlq(data, pos):
    value = data[pos] & 0x7F
    if data[pos] & 0x80:
        pos += 1
        value = (value << 7) | (data[pos] & 0x7F)
    return value, pos + 1


def track_events(body):
    pos, tick, out = 0, 0, []
    while pos < len(body):
        delta, pos = vlq(body, pos)
        tick += delta
        status = body[pos]
        pos += 1
        if status == 0xFF:
            kind, length = body[pos], body[pos + 1]
            out.append(Event(tick, ("meta", kind), body[pos + 2:pos + 2 + length]))
            pos += 2 + length
        elif status == 0xF0:
            length, pos = vlq(body, pos)
            out.append(Event(tick, ("sysex",), body[pos:pos + length]))
            pos += length
        else:
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            out.append(Event(tick, ("channel", status >> 4, status & 0xF), body[pos:pos + size]))
            pos += size
    return out


def tracks(data):
    fmt, ntrks, division, pos = header(data)
    out = []
    while pos + 8 <= len(data) and len(out) < ntrks:
        cid, length = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "big")
        out.append(track_events(data[pos + 8:pos + 8 + length]))
        pos += 8 + length
    return fmt, division, out
