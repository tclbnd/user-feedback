"""Singing score: how well a pitch track follows the melody notes."""

PERCUSSION_CHANNEL = 9  # MIDI channel 10; those notes are not singable


def note_at(notes, t):
    """The melody note sounding at ``t``, or None.

    Notes may overlap (chords, several tracks), so the innermost note --
    the one that started last while still covering ``t`` -- wins.  Ties go
    to the lower pitch, which keeps the choice stable for chords.
    Percussion notes are ignored: they are not part of the melody, and no
    sung pitch can match them.
    """
    best = None
    for n in notes:
        if n.get("channel") == PERCUSSION_CHANNEL:
            continue
        start, end = n["start"], n["end"]
        if start <= t < end:
            if best is None or (start, -n["pitch"]) > (best["start"], -best["pitch"]):
                best = n
    return best


def _pitch_distance(a, b):
    """Distance in semitones between two pitches, ignoring octaves."""
    return abs((a - b + 6) % 12 - 6)


def score(notes, pitch_track, tolerance=1.0):
    """pitch_track: list of (seconds, midi_pitch or None).

    Returns the ratio of melody frames the singer matched.  Frames outside
    every melody note do not count; frames where the singer is silent do
    count against the score.
    """
    hits = total = 0
    for t, p in pitch_track:
        n = note_at(notes, t)
        if n is None:
            continue
        total += 1
        if p is not None and _pitch_distance(p, n["pitch"]) <= tolerance:
            hits += 1
    return round(hits / total, 4) if total else 0.0
