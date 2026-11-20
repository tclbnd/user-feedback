"""Summarise YUV4MPEG2 files as JSON.

    python3 y4minfo.py clip.y4m ...
    python3 y4minfo.py --frame N clip.y4m out.yuv     write the raw planes of frame N (0-based)
"""
import json
import sys

from y4mlab.probe import frame, probe
from y4mlab.stream import Y4mError


def main(paths):
    if paths and paths[0] == "--frame":
        try:
            raw = frame(open(paths[2], "rb").read(), int(paths[1]))
        except (Y4mError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}))
            sys.exit(1)
        open(paths[3], "wb").write(raw)
        print(json.dumps({"bytes": len(raw)}))
        return
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **probe(fh.read())})
        except (Y4mError, OSError, ValueError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
