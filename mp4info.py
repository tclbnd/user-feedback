"""Summarise MP4 and M4A metadata as JSON.

    python3 mp4info.py album.m4a [more files ...]
"""
import json
import struct
import sys

from mp4kit.tags import Mp4Error, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, Mp4Error, ValueError, IndexError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
