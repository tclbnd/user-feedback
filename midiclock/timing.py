"""Ticks to seconds, notes and lyrics.

Times are derived from the Standard MIDI File time base:

* metrical time (``division`` with the high bit clear) counts ticks per
  quarter note and is converted with the tempo map (``us_per_quarter``);
* time-code based time (high bit set) counts ticks per SMPTE frame and is
  independent of any tempo events.

In format 1 the tempo map of the whole file lives on the conductor track
(track 0), and it times every other track.  When that track carries no tempo
events the other tracks' tempo events are used instead.  Format 2 tracks are
independent sequences, so each one keeps its own tempo map.
"""
from bisect import bisect_right
from collections import deque
from fractions import Fraction

from .smf import MidiError, tracks

DEFAULT_TEMPO = 500000  # microseconds per quarter note
_DECIMALS = 10 ** 6


def _round_seconds(value):
    """Round an exact ``Fraction`` of seconds to six decimals (half to even)."""
    scaled = value * _DECIMALS
    whole = scaled.numerator // scaled.denominator
    remainder = scaled - whole
    if remainder > Fraction(1, 2) or (remainder == Fraction(1, 2) and whole % 2):
        whole += 1
    return float(Fraction(whole, _DECIMALS))


def _tempo_changes(events):
    """Tempo events of a track as ``(tick, position, us_per_quarter)``."""
    changes = []
    for pos, event in enumerate(events):
        if event.kind == ("meta", 0x51) and len(event.data) == 3:
            tempo = int.from_bytes(event.data, "big")
            if tempo > 0:
                changes.append((event.tick, pos, tempo))
    return changes


def _metrical_map(changes, division):
    """Return ``(segment_list, seconds_function)`` for ticks-per-quarter time."""
    segments = []  # (tick, us_per_quarter, seconds_at_tick)
    previous_tick, previous_tempo, elapsed = 0, DEFAULT_TEMPO, Fraction(0)
    for tick, _pos, tempo in changes:
        if tick < previous_tick:
            continue
        if segments and segments[-1][0] == tick:
            # Several tempo events on one tick: the last one wins.
            segments.pop()
        else:
            elapsed += Fraction((tick - previous_tick) * previous_tempo, division * 1000000)
        segments.append((tick, tempo, elapsed))
        previous_tick, previous_tempo = tick, tempo
    if not segments or segments[0][0] != 0:
        segments.insert(0, (0, DEFAULT_TEMPO, Fraction(0)))

    ticks = [segment[0] for segment in segments]

    def seconds(tick):
        index = bisect_right(ticks, tick) - 1
        if index < 0:  # negative ticks are meaningless; clamp to the start
            return Fraction(0)
        start_tick, tempo, start_seconds = segments[index]
        return start_seconds + Fraction((tick - start_tick) * tempo, division * 1000000)

    return segments, seconds


def _smpte_frames_per_second(high_byte):
    signed = high_byte - 256
    if signed == -29:  # 29.97 drop frame
        return Fraction(30000, 1001)
    return Fraction(abs(signed))


def _smpte_map(division):
    """Return a ``seconds_function`` for time-code based time."""
    frames = _smpte_frames_per_second(division >> 8)
    ticks_per_frame = division & 0xFF
    if not frames or not ticks_per_frame:
        raise MidiError("bad SMPTE division 0x%04X" % division)
    ticks_per_second = frames * ticks_per_frame

    def seconds(tick):
        return Fraction(tick) / ticks_per_second

    return seconds


def _timing(division, changes):
    """Build ``(tempo_map_entries, seconds)`` for one timing axis."""
    if division & 0x8000:
        # Time-code based: seconds depend only on the division, not on tempo.
        seconds = _smpte_map(division)
        entries = [{"tick": tick, "us_per_quarter": tempo,
                    "seconds": _round_seconds(seconds(tick))}
                   for tick, _pos, tempo in changes]
        if not entries:
            entries = [{"tick": 0, "us_per_quarter": DEFAULT_TEMPO, "seconds": 0.0}]
    else:
        segments, seconds = _metrical_map(changes, division)
        entries = [{"tick": tick, "us_per_quarter": tempo, "seconds": _round_seconds(at)}
                   for tick, tempo, at in segments]
    return entries, seconds


def _track_notes(index, events, seconds):
    notes, open_notes = [], {}

    def close(channel, pitch, tick):
        queue = open_notes.get((channel, pitch))
        if not queue:
            return
        start_tick, velocity = queue.popleft()
        if not queue:
            del open_notes[(channel, pitch)]
        notes.append({"track": index, "channel": channel, "pitch": pitch,
                      "velocity": velocity,
                      "start": _round_seconds(seconds(start_tick)),
                      "end": _round_seconds(seconds(tick))})

    for event in events:
        if event.kind[0] != "channel":
            continue
        high, channel = event.kind[1], event.kind[2]
        pitch, velocity = event.data[0], event.data[1] if len(event.data) > 1 else 0
        if high == 0x9:
            if velocity:
                open_notes.setdefault((channel, pitch), deque()).append((event.tick, velocity))
            else:  # note on with velocity 0 is a note off
                close(channel, pitch, event.tick)
        elif high == 0x8:
            close(channel, pitch, event.tick)

    # Notes left hanging are closed at the end of their track.
    end_tick = events[-1].tick if events else 0
    for channel, pitch in list(open_notes):
        while open_notes.get((channel, pitch)):
            close(channel, pitch, end_tick)
    return notes


def analyse(data):
    fmt, ntrks, division, trks = tracks(data)

    # Note and lyric times are looked up per track.  Format 0 and 1 share one
    # tempo map (in format 1 it belongs to the conductor track, track 0); each
    # format 2 track is an independent sequence with its own map.
    if fmt == 2:
        axes = list(range(len(trks)))
        changes_for = lambda index: sorted(_tempo_changes(trks[index]))
    else:
        axes = [None]
        merged = _tempo_changes(trks[0]) if trks else []
        if not merged:
            # No tempo on the conductor track: fall back to every track's
            # tempo events, in track order.
            merged = sorted(change for events in trks for change in _tempo_changes(events))
        changes_for = lambda index: merged

    axes_seconds, tempo_map = {}, []
    for axis in axes:
        entries, seconds = _timing(division, changes_for(axis))
        axes_seconds[axis] = seconds
        for order, entry in enumerate(entries):
            tempo_map.append((entry["tick"], axis or 0, order, entry))
    # Tempo map in tick order; within a tick, format 2 track order.
    tempo_map.sort(key=lambda item: (item[0], item[1], item[2]))
    tempo_map = [entry for _tick, _axis, _order, entry in tempo_map]

    notes, lyrics = [], []
    for index, events in enumerate(trks):
        seconds = axes_seconds[index if fmt == 2 else None]
        notes.extend(_track_notes(index, events, seconds))
        for pos, event in enumerate(events):
            if event.kind == ("meta", 0x05):
                lyrics.append((event.tick, index, pos,
                               {"seconds": _round_seconds(seconds(event.tick)),
                                "text": event.data.decode("latin-1")}))

    notes.sort(key=lambda n: (n["start"], n["track"], n["pitch"]))
    lyrics.sort(key=lambda item: (item[0], item[1], item[2]))
    lyrics = [lyric for _tick, _track, _pos, lyric in lyrics]
    return {"format": fmt, "division": division, "tracks": ntrks, "tempo_map": tempo_map,
            "notes": notes, "lyrics": lyrics}
