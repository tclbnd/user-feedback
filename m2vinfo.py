"""Summarise MPEG video elementary streams (.m2v, .m1v, .mpv) as JSON.

    python3 m2vinfo.py clip.m2v [more files ...]
"""
import json
import sys

from m2vkit.stream import StreamError, summary


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **summary(fh.read())})
        except (StreamError, OSError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
