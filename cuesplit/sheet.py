"""Cue sheet parsing (CDRWIN cue format).

Times in a cue sheet are written ``mm:ss:ff``, where ``ff`` counts CD
frames of 1/75 second.  At 44.1 kHz a frame is exactly 588 samples, so
every cue time maps to a whole number of samples without rounding.
Times are always relative to the FILE they appear in; discs with more
than one FILE (e.g. a two-disc rip) each start again at 00:00:00.
"""
SAMPLES_PER_SECOND = 44100
FRAMES_PER_SECOND = 75
SAMPLES_PER_FRAME = SAMPLES_PER_SECOND // FRAMES_PER_SECOND  # 588


class CueError(ValueError):
    pass


def msf(text):
    """``mm:ss:ff`` -> samples (frames are 1/75 s; minutes may exceed 99)."""
    parts = text.split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise CueError(f"bad time {text!r}")
    minutes, seconds, frames = (int(p) for p in parts)
    # Minutes may have more than two digits (a cue can run past 99 minutes);
    # seconds and frames are always two digits.
    if len(parts[1]) != 2 or len(parts[2]) != 2:
        raise CueError(f"bad time {text!r}")
    if len(parts[0]) < 1:
        raise CueError(f"bad time {text!r}")
    if seconds > 59:
        raise CueError(f"bad time {text!r}: seconds out of range")
    if frames >= FRAMES_PER_SECOND:
        raise CueError(f"bad time {text!r}: frame out of range")
    return (minutes * 60 + seconds) * SAMPLES_PER_SECOND + frames * SAMPLES_PER_FRAME


def _tokens(line):
    """Split a cue line into tokens, keeping quoted values intact.

    Quotes are how cue sheets protect titles and file names that contain
    spaces; everything between the quotes is one token.
    """
    tokens, i, n = [], 0, len(line)
    while i < n:
        if line[i].isspace():
            i += 1
        elif line[i] == '"':
            end = line.find('"', i + 1)
            if end < 0:
                raise CueError(f"unterminated quote in {line.strip()!r}")
            tokens.append(line[i + 1:end])
            i = end + 1
        else:
            end = i
            while end < n and not line[end].isspace():
                end += 1
            tokens.append(line[i:end])
            i = end
    return tokens


def _add_rem(target, rest):
    target["rem_lines"].append(rest)
    parts = _tokens(rest)
    if parts:
        target["rem"][parts[0].upper()] = " ".join(parts[1:])


def _new_track(number, kind):
    return {
        "number": number,
        "type": kind,
        "title": None,
        "performer": None,
        "isrc": None,
        "indexes": {},       # index number -> samples within index_files[n]
        "index_files": {},   # index number -> name of the FILE it appears in
        "pregap": None,      # PREGAP command, samples (not in the image file)
        "postgap": None,     # POSTGAP command, samples (not in the image file)
        "rem": {},
        "rem_lines": [],
    }


def parse(text):
    album = {
        "title": None, "performer": None, "catalog": None,
        "rem": {}, "rem_lines": [], "files": [],
    }
    current_file = None
    track = None         # scope for TITLE/PERFORMER/ISRC/PREGAP/REM
    index_target = None  # last TRACK; an INDEX may follow a new FILE (gap over two files)
    for raw in text.lstrip("﻿").splitlines():
        tokens = _tokens(raw)
        if not tokens:
            continue
        key, args = tokens[0].upper(), tokens[1:]

        if key == "FILE":
            if not args:
                raise CueError("FILE without a name")
            if len(args) == 1:
                name, kind = args[0], "WAVE"
            else:
                name, kind = " ".join(args[:-1]), args[-1].upper()
            current_file = {"name": name, "type": kind, "tracks": []}
            album["files"].append(current_file)
            # A new FILE changes the time axis, but the current track is not
            # over: a pregap may start in the old file and its INDEX 01 appear
            # in the new one.
            track = None

        elif key == "TRACK":
            if current_file is None:
                raise CueError("TRACK before FILE")
            if len(args) < 2:
                raise CueError("TRACK needs a number and a type")
            try:
                number = int(args[0])
            except ValueError:
                raise CueError(f"bad track number {args[0]!r}") from None
            if not 1 <= number <= 99:
                raise CueError(f"bad track number {args[0]!r}")
            track = _new_track(number, args[1].upper())
            current_file["tracks"].append(track)
            index_target = track

        elif key == "INDEX":
            if index_target is None:
                raise CueError("INDEX before TRACK")
            if len(args) != 2:
                raise CueError("INDEX needs a number and a time")
            try:
                number = int(args[0])
            except ValueError:
                raise CueError(f"bad index number {args[0]!r}") from None
            if not 0 <= number <= 99:
                raise CueError(f"bad index number {args[0]!r}")
            index_target["indexes"][number] = msf(args[1])
            index_target["index_files"][number] = current_file["name"]

        elif key in ("PREGAP", "POSTGAP"):
            if track is None:
                raise CueError(f"{key} before TRACK")
            if len(args) != 1:
                raise CueError(f"{key} needs a time")
            track[key.lower()] = msf(args[0])

        elif key in ("TITLE", "PERFORMER"):
            if not args:
                raise CueError(f"{key} without a value")
            target = track if track is not None else album
            target[key.lower()] = " ".join(args)

        elif key == "ISRC":
            if track is None:
                raise CueError("ISRC before TRACK")
            if len(args) != 1:
                raise CueError("ISRC needs a value")
            track["isrc"] = args[0]

        elif key == "CATALOG":
            if args:
                album["catalog"] = args[0]

        elif key == "REM":
            stripped = raw.strip()
            _add_rem(track if track is not None else album,
                     stripped[len(tokens[0]):].strip())

        # Unknown commands (FLAGS, SONGWRITER, CDTEXTFILE, ...) are ignored.

    for f in album["files"]:
        for t in f["tracks"]:
            if 1 not in t["indexes"]:
                raise CueError(f"track {t['number']:02d} has no INDEX 01")
            if t["performer"] is None:
                t["performer"] = album["performer"]
    return album
