"""Cue sheet parsing, following the CDRWIN cue sheet format.

A sheet is one command per line.  String arguments may be quoted with double
quotes so they can contain spaces; a doubled quote (``""``) inside a quoted
string stands for a literal quote.  Times are written ``mm:ss:ff`` where
``ff`` counts CD frames of 1/75 second and are converted to samples at
44.1 kHz.

Each INDEX is remembered together with the file it was written in, because a
ripper may put a track's INDEX 00 (the gap) at the end of one image and its
INDEX 01 (the audio) at the start of the next, or write the FILE command
between the two.
"""
import re

SAMPLES_PER_SECOND = 44100
FRAMES_PER_SECOND = 75
SAMPLES_PER_FRAME = SAMPLES_PER_SECOND // FRAMES_PER_SECOND  # 588

_TIME = re.compile(r"([0-9]+):([0-9]+):([0-9]+)")
_FILE_TYPES = {"WAVE", "MP3", "AIFF", "BINARY", "MOTOROLA", "FLAC"}


class CueError(ValueError):
    pass


def msf(text):
    """mm:ss:ff -> samples at 44.1 kHz.

    ``ff`` is a CD frame, 1/75 of a second, not 1/100.
    """
    text = (text or "").strip()
    match = _TIME.fullmatch(text)
    if match is None:
        raise CueError(f"bad time {text!r}")
    minutes, seconds, frames = (int(part) for part in match.groups())
    if seconds >= 60:
        raise CueError(f"bad time {text!r}: seconds out of range")
    if frames >= FRAMES_PER_SECOND:
        raise CueError(f"bad time {text!r}: frames out of range")
    return (minutes * 60 + seconds) * SAMPLES_PER_SECOND + frames * SAMPLES_PER_FRAME


def _quoted(text):
    """Read one (possibly quoted) token, returning it and the rest of the line.

    ``FILE "Harbour Lights.wav" WAVE`` gives ``("Harbour Lights.wav", "WAVE")``.
    """
    text = (text or "").lstrip()
    if not text:
        return "", ""
    if text[0] != '"':
        parts = text.split(None, 1)
        return parts[0], (parts[1] if len(parts) > 1 else "")
    out = []
    i = 1
    while i < len(text):
        char = text[i]
        if char == '"':
            if i + 1 < len(text) and text[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            return "".join(out), text[i + 1:]
        out.append(char)
        i += 1
    return "".join(out), ""


def _string(text):
    """The rest of a command line as a string, quoted or not."""
    text = (text or "").strip()
    if not text:
        return None
    if text[0] == '"':
        value, _rest = _quoted(text)
        return value
    return text


def _file(text):
    """FILE name and type.

    The name is normally quoted so it may contain spaces; the type follows
    unquoted.  A few rippers leave the name unquoted, in which case a
    trailing word that looks like a type is taken as one.
    """
    text = (text or "").strip()
    if not text:
        return "", None
    if text[0] == '"':
        name, rest = _quoted(text)
        rest = rest.strip()
        return name, rest.split(None, 1)[0] if rest else None
    head, sep, tail = text.rpartition(" ")
    if sep and tail.upper() in _FILE_TYPES:
        return head, tail
    return text, None


def _int(text, what, low=0, high=99):
    text = (text or "").strip()
    try:
        value = int(text, 10)
    except ValueError:
        raise CueError(f"bad {what} {text!r}") from None
    if not low <= value <= high:
        raise CueError(f"bad {what} {text!r}")
    return value


def _last_track(album):
    for file_ in reversed(album["files"]):
        if file_["tracks"]:
            return file_["tracks"][-1]
    return None


def _adopt(album, previous, track):
    """Fold a track whose audio starts in the next file into ``track``.

    Some rippers write a track that spans two images as one TRACK command
    before each FILE command, with INDEX 00 in the first image and INDEX 01
    in the second.  That is one track, with the gap of the first image.
    """
    for field in ("title", "performer", "isrc", "pregap"):
        if track[field] is None:
            track[field] = previous[field]
    track["indexes"].update(previous["indexes"])
    track["index_files"].update(previous["index_files"])
    for file_ in album["files"]:
        for i, candidate in enumerate(file_["tracks"]):
            if candidate is previous:
                del file_["tracks"][i]
                return


def parse(text):
    """Parse a cue sheet into an album of files, each holding its tracks.

    Index times are kept in samples together with the file they were written
    in, since a gap may close one image and its track open the next.
    """
    album = {"title": None, "performer": None, "files": []}
    current_file = None
    track = None         # track whose block is being read
    carried = None       # last track of the previous image, still open
    for raw in text.lstrip(chr(0xFEFF)).splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        parts = line.split(None, 1)
        key = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        if key == "REM":
            continue
        if key == "FILE":
            name, kind = _file(rest)
            if not name:
                raise CueError("FILE without a name")
            current_file = {"name": name, "type": kind, "tracks": []}
            album["files"].append(current_file)
            # A track whose INDEX 00 closed the previous image may receive
            # its INDEX 01 here, after the FILE, before any TRACK.
            carried = track if track is not None and 1 not in track["indexes"] else None
            track = None
        elif key == "TRACK":
            if current_file is None:
                raise CueError("TRACK before FILE")
            number_text, rest = _quoted(rest)
            number = _int(number_text, "track number", 1, 99)
            track = {
                "number": number,
                "type": _string(rest),
                "title": None,
                "performer": None,
                "isrc": None,
                "indexes": {},
                "index_files": {},
                "pregap": None,
                "file": current_file["name"],
            }
            # Some rippers repeat the TRACK command in the next image for a
            # track that spans both, with INDEX 00 in the first and INDEX 01
            # in the second.  That is one track, and its gap is the first.
            previous = carried if carried is not None else _last_track(album)
            carried = None
            if (previous is not None and previous["number"] == number
                    and previous["file"] != current_file["name"]
                    and 1 not in previous["indexes"]):
                _adopt(album, previous, track)
            current_file["tracks"].append(track)
        elif key == "INDEX":
            target = track if track is not None else carried
            if target is None:
                raise CueError("INDEX before TRACK")
            number_text, rest = _quoted(rest)
            number = _int(number_text, "index number")
            value = _string(rest)
            if value is None:
                raise CueError("INDEX without a time")
            target["indexes"][number] = msf(value)
            target["index_files"][number] = current_file["name"]
            track, carried = target, None
        elif key == "PREGAP":
            target = track if track is not None else carried
            if target is None:
                raise CueError("PREGAP before TRACK")
            value = _string(rest)
            if value is None:
                raise CueError("PREGAP without a time")
            target["pregap"] = msf(value)
            track, carried = target, None
        elif key in ("TITLE", "PERFORMER"):
            target = track if track is not None else album
            target[key.lower()] = _string(rest)
        elif key == "ISRC":
            target = track if track is not None else carried
            if target is not None:
                target["isrc"] = _string(rest)
    # A track without PERFORMER of its own is by the album's performer.
    for file_ in album["files"]:
        for track in file_["tracks"]:
            if not track["performer"]:
                track["performer"] = album["performer"]
    return album
