"""Track layout of a cue sheet as JSON.

    python3 cueinfo.py album.cue [--length "CD1.wav=12345678" ...]

--length gives the sample count of an audio file so the last track of that
file gets an end.
"""
import json
import sys

from cuesplit.sheet import CueError, parse
from cuesplit.tracks import layout


def main(args):
    path, lengths = args[0], {}
    rest = args[1:]
    while rest:
        if rest[0] == "--length":
            name, _, n = rest[1].rpartition("=")
            lengths[name] = int(n)
            rest = rest[2:]
        else:
            rest = rest[1:]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    try:
        album = parse(text)
        print(json.dumps({"file": path, "title": album["title"], "performer": album["performer"],
                          "tracks": layout(album, lengths)}, ensure_ascii=False, indent=1))
    except CueError as exc:
        print(json.dumps({"file": path, "error": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
