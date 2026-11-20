"""Ticks to seconds, notes and lyrics.

Timing follows Standard MIDI File 1.0.  A file either uses a PPQ
(pulses per quarter note) division, in which case tempo meta events shape
a tempo map, or an SMPTE division, in which case ticks are a fixed
fraction of a second and tempo events do not affect timing.  For format 1
the tempo map is global: tempo meta events may live in any track (in
practice track 0) and apply to every track.  Format 2 tracks are
independent sequences, so each gets its own tempo map.
"""
from bisect import bisect_right
from collections import deque
from fractions import Fraction

from .smf import MidiError, tracks

DEFAULT_TEMPO = 500000  # microseconds per quarter note (120 BPM)

_SMPTE_RATES = {
    0xE8: Fraction(24),          # -24
    0xE7: Fraction(25),          # -25
    0xE3: Fraction(30000, 1001),  # -29, 29.97 fps drop frame
    0xE2: Fraction(30),          # -30
}


def smpte_timebase(division):
    """Return a tick-to-seconds function for an SMPTE division, else None."""
    if not division & 0x8000:
        return None
    ticks_per_frame = division & 0xFF
    fps = _SMPTE_RATES.get((division >> 8) & 0xFF)
    if fps is None:
        raise MidiError("unsupported SMPTE frame rate 0x%02X" % ((division >> 8) & 0xFF))
    if ticks_per_frame == 0:
        raise MidiError("invalid SMPTE time division")
    return lambda tick: Fraction(tick) / (fps * ticks_per_frame)


def tempo_events(events):
    """Return ``(tick, microseconds_per_quarter)`` for tempo meta events.

    The spec fixes the payload at three bytes.  Events with another length
    are ignored rather than fatal: a damaged tempo event should not stop a
    whole song from being timed.
    """
    out = []
    for e in events:
        if e.kind == ("meta", 0x51) and len(e.data) == 3:
            value = int.from_bytes(e.data, "big")
            if value > 0:  # a zero tempo would stall the clock; ignore it
                out.append((e.tick, value))
    return out


class Clock:
    """Converts absolute ticks to seconds."""

    def __init__(self, division, tempos=()):
        self.division = division
        self.smpte = smpte_timebase(division)
        self._tempos = list(tempos)
        self._starts, self._seconds, self._values = [], [], []
        if self.smpte is None:
            self._build()

    def _build(self):
        merged = []
        for tick, value in sorted(self._tempos, key=lambda item: item[0]):
            if merged and merged[-1][0] == tick:
                merged[-1] = (tick, value)  # last tempo at a tick wins
            else:
                merged.append((tick, value))
        if not merged or merged[0][0] > 0:
            merged.insert(0, (0, DEFAULT_TEMPO))

        seconds, prev_tick, prev_value = Fraction(0), 0, DEFAULT_TEMPO
        for tick, value in merged:
            seconds += Fraction((tick - prev_tick) * prev_value, self.division * 1000000)
            self._starts.append(tick)
            self._seconds.append(seconds)
            self._values.append(value)
            prev_tick, prev_value = tick, value

    def seconds(self, tick):
        """Seconds from the start of the sequence at ``tick``."""
        if self.smpte is not None:
            return self.smpte(tick)
        index = bisect_right(self._starts, tick) - 1
        return self._seconds[index] + Fraction(
            (tick - self._starts[index]) * self._values[index], self.division * 1000000
        )

    def tempo_map(self):
        """The tempo map as plain, JSON-friendly records."""
        if self.smpte is not None:
            # Tempo meta events do not affect an SMPTE-based clock; report
            # them for reference, in tick order.
            raw = sorted(self._tempos, key=lambda item: item[0])
        else:
            raw = list(zip(self._starts, self._values))
        return [
            {"tick": tick, "us_per_quarter": value, "seconds": _round(self.seconds(tick))}
            for tick, value in raw
        ]


def _round(value):
    return round(float(value), 6)


def _text(payload):
    """Decode lyric bytes.

    SMF does not prescribe an encoding.  Catalogue files are mostly ASCII
    or UTF-8, with older ones in a local single-byte code page; UTF-8 is
    tried first and latin-1 is the lossless fallback.
    """
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload.decode("latin-1")


def _close_note(open_notes, notes, clock, track, channel, pitch, tick):
    queue = open_notes.get((channel, pitch))
    if not queue:
        return
    start, velocity = queue.popleft()
    if not queue:
        del open_notes[(channel, pitch)]
    notes.append({
        "track": track,
        "channel": channel,
        "pitch": pitch,
        "velocity": velocity,
        "start": _round(clock.seconds(start)),
        "end": _round(clock.seconds(max(tick, start))),
    })


def _notes(events, clock, track):
    """Pair note-on/note-off events of one track into notes in seconds."""
    notes, open_notes = [], {}
    last_tick = 0
    for e in events:
        last_tick = max(last_tick, e.tick)
        if e.kind[0] != "channel":
            continue
        channel, status = e.kind[2], e.kind[1]
        if status == 0x9:
            pitch, velocity = e.data[0], e.data[1]
            if velocity:
                open_notes.setdefault((channel, pitch), deque()).append((e.tick, velocity))
            else:  # velocity 0 note-on is a note-off
                _close_note(open_notes, notes, clock, track, channel, pitch, e.tick)
        elif status == 0x8:
            _close_note(open_notes, notes, clock, track, channel, e.data[0], e.tick)

    # A note that is still on at the end of its track ends with the track.
    for (channel, pitch), queue in list(open_notes.items()):
        while queue:
            _close_note(open_notes, notes, clock, track, channel, pitch, last_tick)
    return notes


def analyse(data):
    fmt, division, trks = tracks(data)
    if division == 0:
        raise MidiError("invalid time division")

    if fmt == 2:
        # Each track is an independent sequence with its own tempo map.
        clocks = [Clock(division, tempo_events(events)) for events in trks]
    else:
        clocks = [Clock(division, tempo_events(e for events in trks for e in events))] * len(trks)

    notes, lyrics, tempo_map = [], [], []
    for track, events in enumerate(trks):
        clock = clocks[track]
        if fmt == 2:
            tempo_map.extend(dict(entry, track=track) for entry in clock.tempo_map())
        notes.extend(_notes(events, clock, track))
        for e in events:
            if e.kind == ("meta", 0x05):
                lyrics.append({"seconds": _round(clock.seconds(e.tick)),
                               "text": _text(e.data)})

    if fmt != 2:
        tempo_map = clocks[0].tempo_map() if clocks else Clock(division).tempo_map()

    notes.sort(key=lambda n: (n["start"], n["track"], n["pitch"]))
    lyrics.sort(key=lambda l: l["seconds"])
    return {"format": fmt, "division": division, "tracks": len(trks), "tempo_map": tempo_map,
            "notes": notes, "lyrics": lyrics}
