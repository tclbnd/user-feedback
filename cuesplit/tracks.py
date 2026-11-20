"""Track start, end and length in samples, relative to each track's file.

Cue times are per FILE: every file starts its own clock at 00:00:00, so
tracks of a second disc are not offset by the length of the first one.
A track starts at its INDEX 01.  It ends at the next track's INDEX 01 in
the same file, which keeps the gap (INDEX 00 of the next track) at the
end of the previous track, as the archive convention requires.  When
there is no next INDEX 01 in the file (last track, or the next track
starts in another file) the end is the length of the file, if known.
"""


def _pregap(track, lengths):
    """Gap before INDEX 01, whether it is in the image file or not.

    A PREGAP command is silence the burner generates, so it is not in the
    file.  Otherwise INDEX 00 marks the start of the gap in the image.
    The gap can begin in one file and end in the next one (a pregap split
    over two files); the tail of the first file belongs to it too, so the
    first file's length is needed to measure it.
    """
    if track["pregap"] is not None:
        return track["pregap"]
    indexes = track["indexes"]
    if 0 not in indexes:
        return 0
    start = indexes[0]
    frame_file = track["index_files"][0]
    if frame_file == track["index_files"][1]:
        return indexes[1] - start
    length = lengths.get(frame_file)
    if length is None:
        return None
    return length - start + indexes[1]


def layout(album, lengths=None):
    lengths = lengths or {}
    flat = [t for f in album["files"] for t in f["tracks"]]
    out = []
    for i, t in enumerate(flat):
        name = t["index_files"][1]
        start = t["indexes"][1]
        end = None
        if i + 1 < len(flat):
            following = flat[i + 1]
            if following["index_files"][1] == name:
                end = following["indexes"][1]
        if end is None:
            end = lengths.get(name)
        out.append({
            "number": t["number"],
            "title": t["title"],
            "performer": t["performer"],
            "isrc": t["isrc"],
            "file": name,
            "start": start,
            "end": end,
            "length": end - start if end is not None else None,
            "pregap": _pregap(t, lengths),
            "postgap": t["postgap"] or 0,
        })
    return out
