"""Summarise ScreamTracker 3 modules (.s3m) as JSON.

    python3 s3minfo.py song.s3m [more files ...]
"""
import json
import struct
import sys

from s3mkit.s3m import S3mError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, S3mError, IndexError, ValueError, struct.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
