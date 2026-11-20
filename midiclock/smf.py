"""Standard MIDI File chunks and track events.

The parser follows the Standard MIDI File 1.0 specification: variable
length quantities, running status for channel messages, meta events with
variable length payloads, and both F0 and F7 system exclusive events.
Chunks other than ``MTrk`` are skipped by their declared length so that
files carrying specification-external chunks still parse.

One deliberate relaxation: the spec cancels running status when a meta or
sysex event interrupts a channel message stream, but karaoke tracks in
the catalogue routinely interleave lyric meta events and keep using the
previous channel status.  Keeping that status in force makes those files
read correctly.
"""
from collections import namedtuple

Event = namedtuple("Event", "tick kind data")


class MidiError(ValueError):
    pass


def header(data):
    """Return ``(format, ntracks, division, offset_of_first_chunk)``."""
    if len(data) < 8 or data[:4] != b"MThd":
        raise MidiError("not a MIDI file")
    length = int.from_bytes(data[4:8], "big")
    if length < 6:
        raise MidiError("bad MIDI header length")
    end = 8 + length
    if end > len(data):
        raise MidiError("truncated MIDI header")
    fmt = int.from_bytes(data[8:10], "big")
    ntrks = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if fmt not in (0, 1, 2):
        raise MidiError("unsupported MIDI format %d" % fmt)
    return fmt, ntrks, division, end


def _unwrap(data):
    """Strip a RIFF RMID wrapper, returning the bare SMF image."""
    if data[:4] == b"RIFF" and data[8:12] == b"RMID":
        pos = 12
        while pos + 8 <= len(data):
            kind = data[pos:pos + 4]
            length = int.from_bytes(data[pos + 4:pos + 8], "big")
            if kind == b"data":
                return data[pos + 8:pos + 8 + length]
            pos += 8 + length + (length & 1)  # RIFF chunks are word aligned
    return data


def vlq(data, pos):
    """Read a variable length quantity of one to four bytes."""
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("truncated variable-length quantity")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("variable-length quantity is longer than four bytes")


def track_events(body):
    """Parse the body of one ``MTrk`` chunk into a list of :class:`Event`."""
    pos, tick, out = 0, 0, []
    running = None
    while pos < len(body):
        delta, pos = vlq(body, pos)
        tick += delta
        if pos >= len(body):
            raise MidiError("truncated track event")

        status = body[pos]
        if status & 0x80:
            pos += 1
        else:
            # Running status: this byte is the first data byte of the
            # previous channel message.  The spec cancels running status
            # when a meta or sysex event intervenes, but writers of lyric
            # tracks often keep relying on it across the lyric meta
            # events, so the last channel status stays in force here.
            # Only a data byte with no earlier channel message is an error.
            if running is None:
                raise MidiError("running status without a previous status")
            status = running

        if status == 0xFF:
            if pos >= len(body):
                raise MidiError("truncated meta event")
            kind = body[pos]
            pos += 1
            length, pos = vlq(body, pos)
            end = pos + length
            if end > len(body):
                raise MidiError("truncated meta event payload")
            out.append(Event(tick, ("meta", kind), body[pos:end]))
            pos = end
            if kind == 0x2F:  # end of track
                break
        elif status in (0xF0, 0xF7):
            length, pos = vlq(body, pos)
            end = pos + length
            if end > len(body):
                raise MidiError("truncated sysex event payload")
            out.append(Event(tick, ("sysex",), body[pos:end]))
            pos = end
        elif 0x80 <= status <= 0xEF:
            running = status
            size = 1 if status >> 4 in (0xC, 0xD) else 2
            end = pos + size
            if end > len(body):
                raise MidiError("truncated channel event")
            payload = body[pos:end]
            if any(byte & 0x80 for byte in payload):
                raise MidiError("invalid data byte in channel event")
            out.append(Event(tick, ("channel", status >> 4, status & 0x0F), payload))
            pos = end
        else:
            raise MidiError("unsupported status byte 0x%02X" % status)
    return out


def tracks(data):
    """Return ``(format, division, tracks)`` for a complete SMF image.

    Every ``MTrk`` chunk in the file is returned, even when the header's
    track count disagrees.  Unknown chunks (and trailing padding) are
    skipped by their declared length, so specification-external chunks do
    not shift the following tracks.  A ``.rmi``/RMID file is unwrapped
    first.
    """
    data = _unwrap(data)
    fmt, ntrks, division, pos = header(data)
    out = []
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        length = int.from_bytes(data[pos + 4:pos + 8], "big")
        start = pos + 8
        end = start + length
        if end > len(data):
            # Trailing junk with a bogus length is ignored, but a track
            # that runs off the end of the file is a real truncation.
            if chunk_id == b"MTrk":
                raise MidiError("truncated MTrk chunk")
            break
        if chunk_id == b"MTrk":
            out.append(track_events(data[start:end]))
        pos = end
    return fmt, division, out