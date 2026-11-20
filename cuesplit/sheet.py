"""Cue sheet parsing."""
SAMPLES_PER_SECOND = 44100


class CueError(ValueError):
    pass


def msf(text):
    """mm:ss:ff -> samples."""
    try:
        m, s, f = (int(x) for x in text.split(":"))
    except ValueError:
        raise CueError(f"bad time {text!r}") from None
    return (m * 60 + s) * SAMPLES_PER_SECOND + f * SAMPLES_PER_SECOND // 100


def parse(text):
    album = {"title": None, "performer": None, "files": []}
    current_file, track = None, None
    for raw in text.splitlines():
        words = raw.split()
        if not words:
            continue
        key = words[0]
        if key == "FILE":
            current_file = {"name": words[1].strip('"'), "tracks": []}
            album["files"].append(current_file)
        elif key == "TRACK":
            track = {"number": int(words[1]), "type": words[2], "title": None, "performer": None, "indexes": {}}
            current_file["tracks"].append(track)
        elif key == "INDEX":
            track["indexes"][int(words[1])] = msf(words[2])
        elif key in ("TITLE", "PERFORMER"):
            value = words[1].strip('"')
            target = track if track else album
            target[key.lower()] = value
    return album
