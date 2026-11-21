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
    if not args:
        print("usage: cueinfo.py SHEET [--length FILE=SAMPLES ...]", file=sys.stderr)
        return 2
    path, lengths = args[0], {}
    rest = args[1:]
    while rest:
        arg = rest[0]
        if arg == "--length" and len(rest) > 1:
            spec, rest = rest[1], rest[2:]
        elif arg.startswith("--length="):
            spec, rest = arg.split("=", 1)[1], rest[1:]
        else:
            rest = rest[1:]
            continue
        name, sep, n = spec.rpartition("=")
        try:
            if not sep or not name:
                raise ValueError
            lengths[name] = int(n)
        except ValueError:
            print(f"bad --length {spec!r}", file=sys.stderr)
            return 2
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
        album = parse(text)
        tracks = layout(album, lengths)
    except CueError as exc:
        print(json.dumps({"file": path, "error": str(exc)}, ensure_ascii=False))
        return 1
    except OSError as exc:
        print(json.dumps({"file": path, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"file": path, "title": album["title"], "performer": album["performer"],
                      "tracks": tracks}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
