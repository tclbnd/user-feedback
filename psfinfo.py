"""Summarise PSF family music files as JSON.

    python3 psfinfo.py song.psf [more files ...]
"""
import json
import zlib
import sys

from psfkit.psf import PsfError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, PsfError, ValueError, IndexError, KeyError, zlib.error) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
