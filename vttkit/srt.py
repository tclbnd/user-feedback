"""SubRip export for the legacy players."""


def clock(ms):
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export(cues):
    parts = []
    for i, cue in enumerate(cues, 1):
        parts.append(f"{i}\n{clock(cue['start'])} --> {clock(cue['end'])}\n{cue['text']}\n")
    return "\n".join(parts)
