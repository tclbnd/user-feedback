"""Summarise FLAC files as JSON.

    python3 flacinfo.py album.flac [more files ...]
"""
import json
import struct
import sys

from flackit.flac import FlacError, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (FlacError, OSError, struct.error, UnicodeDecodeError, IndexError, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
