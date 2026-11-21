"""Summarise MPEG audio files as JSON.

    python3 mp3info.py song.mp3 [more files ...]
"""
import json
import sys

from mp3kit.mp3 import Mp3Error, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (OSError, Mp3Error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
