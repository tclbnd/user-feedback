"""Track start, end and length in samples, relative to each track's file.

The gap between two tracks belongs to the earlier one: a track runs from its
INDEX 01 to the next track's INDEX 01 in the same file.  ``pregap`` is the
gap before INDEX 01, which may be part of the image (INDEX 00) or only
declared in the sheet (PREGAP).
"""
import os

from .sheet import CueError


def _length_of(name, lengths):
    """Sample count given for a file, by its name in the sheet or basename."""
    if name in lengths:
        return lengths[name]
    return lengths.get(os.path.basename(name))


def _audio_file(track):
    """The file the track's audio starts in.

    INDEX 00 may sit at the end of one image while INDEX 01 opens the next
    one; the audio, and so the track, belongs to the file of its INDEX 01.
    """
    return track["index_files"].get(1) or track["file"]


def _pregap(track, name, start, lengths, first):
    """Samples of gap before INDEX 01.

    INDEX 00 marks a gap that is in the image; PREGAP declares one that is
    not.  A track that opens a file part-way in has the silence before it as
    its gap as well.
    """
    index0 = track["indexes"].get(0)
    if index0 is not None:
        gap_file = track["index_files"].get(0) or name
        if gap_file == name:
            if start < index0:
                raise CueError(f"track {track['number']:02d}: INDEX 01 precedes INDEX 00")
            return start - index0
        # The gap began in an earlier file of the set; count what is left of
        # that file, if its length is known, plus the head of this one.
        length = _length_of(gap_file, lengths)
        if length is None:
            return start
        if length < index0:
            raise CueError(f"track {track['number']:02d}: INDEX 00 is past the end of {gap_file}")
        return length - index0 + start
    if track.get("pregap") is not None:
        return track["pregap"]
    return start if first else 0


def _check_track(track):
    if 1 not in track["indexes"]:
        raise CueError(f"track {track['number']:02d} has no INDEX 01")


def layout(album, lengths=None):
    """Per-track boundaries, in cue order, within each track's file."""
    lengths = lengths or {}
    by_file, order = {}, []
    for file_ in album["files"]:
        for track in file_["tracks"]:
            _check_track(track)
            name = _audio_file(track)
            if name not in by_file:
                by_file[name] = []
                order.append(name)
            by_file[name].append(track)

    bounds = {}
    for name in order:
        # Tracks of one file appear in it in playing order, so each INDEX 01
        # must be at or after the one before.
        tracks = by_file[name]
        for i, track in enumerate(tracks):
            start = track["indexes"][1]
            if i:
                previous = tracks[i - 1]["indexes"][1]
                if start < previous:
                    raise CueError(f"track {track['number']:02d}: INDEX 01 goes backwards")
            if i + 1 < len(tracks):
                end = tracks[i + 1]["indexes"][1]
            else:
                end = _length_of(name, lengths)
                if end is not None and end < start:
                    raise CueError(f"track {track['number']:02d}: starts past the end of {name}")
            bounds[id(track)] = (name, start, end, i == 0)

    out = []
    for file_ in album["files"]:
        for track in file_["tracks"]:
            name, start, end, first = bounds[id(track)]
            out.append({
                "number": track["number"],
                "title": track["title"],
                "performer": track["performer"],
                "file": name,
                "start": start,
                "end": end,
                "length": end - start if end is not None else None,
                "pregap": _pregap(track, name, start, lengths, first),
                "isrc": track["isrc"],
                "type": track["type"],
            })
    return out
