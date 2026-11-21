"""Standard MIDI File chunks and track events."""
from collections import namedtuple

Event = namedtuple("Event", "tick kind data")


class MidiError(ValueError):
    pass


def header(data):
    """Return ``(format, ntracks, division, offset_of_first_chunk)``."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise MidiError("not a MIDI file")
    length = int.from_bytes(data[4:8], "big")
    if length < 6 or 8 + length > len(data):
        raise MidiError("bad header chunk")
    fmt = int.from_bytes(data[8:10], "big")
    ntrks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if fmt not in (0, 1, 2):
        raise MidiError("unsupported SMF format %d" % fmt)
    if division == 0:
        raise MidiError("bad division")
    # The header chunk may carry extra bytes; the first chunk starts after them.
    return fmt, ntrks, division, 8 + length


def vlq(data, pos):
    """Read a variable-length quantity (at most four 7-bit groups)."""
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("truncated variable-length quantity")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("variable-length quantity longer than four bytes")


def track_events(body):
    pos, tick, out = 0, 0, []
    # Running status: the last channel status byte, reused by following events
    # that omit their status.  Meta and system-exclusive events cancel it.
    running = None
    while pos < len(body):
        delta, pos = vlq(body, pos)
        tick += delta
        if pos >= len(body):
            raise MidiError("truncated event")

        status = body[pos]
        if status & 0x80:
            pos += 1
        else:
            if running is None:
                raise MidiError("data byte without running status")
            status = running

        if status == 0xFF:
            running = None
            if pos >= len(body):
                raise MidiError("truncated meta event")
            kind = body[pos]
            length, pos = vlq(body, pos + 1)
            end = pos + length
            if end > len(body):
                raise MidiError("truncated meta event data")
            out.append(Event(tick, ("meta", kind), body[pos:end]))
            pos = end
        elif status in (0xF0, 0xF7):
            running = None
            length, pos = vlq(body, pos)
            end = pos + length
            if end > len(body):
                raise MidiError("truncated sysex event")
            out.append(Event(tick, ("sysex",), body[pos:end]))
            pos = end
        elif 0x80 <= status <= 0xEF:
            running = status
            high = status >> 4
            size = 1 if high in (0xC, 0xD) else 2
            end = pos + size
            if end > len(body):
                raise MidiError("truncated channel event")
            payload = body[pos:end]
            out.append(Event(tick, ("channel", high, status & 0x0F), payload))
            pos = end
        elif status in (0xF1, 0xF3):
            # System common messages carry one data byte; they cancel running
            # status.  They do not occur in SMF, but tolerate them.
            running = None
            pos += 1
        elif status == 0xF2:
            running = None
            pos += 2
        else:
            # System realtime bytes may be interleaved anywhere and carry no
            # data; ignore them rather than rejecting the whole file.
            pass
    return out


def tracks(data):
    fmt, ntrks, division, pos = header(data)
    out = []
    remaining = len(data) - pos
    while remaining >= 8:
        chunk_id = data[pos:pos + 4]
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        end = pos + 8 + length
        if end > len(data):
            raise MidiError("truncated chunk")
        if chunk_id == b"MTrk":
            out.append(track_events(data[pos + 8:end]))
        pos = end
        remaining = len(data) - pos
        # Unknown chunk types are skipped, as the specification requires.
    return fmt, ntrks, division, out
