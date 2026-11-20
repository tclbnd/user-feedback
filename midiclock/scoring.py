"""Singing score: how well a pitch track follows the melody notes."""


def note_at(notes, t):
    for n in notes:
        if n["start"] <= t < n["end"]:
            return n
    return None


def score(notes, pitch_track, tolerance=1.0):
    """pitch_track: list of (seconds, midi_pitch or None). Returns hit ratio over voiced melody frames."""
    hits = total = 0
    for t, p in pitch_track:
        n = note_at(notes, t)
        if n is None:
            continue
        total += 1
        if p is not None and abs((p - n["pitch"] + 6) % 12 - 6) <= tolerance:
            hits += 1
    return round(hits / total, 4) if total else 0.0
