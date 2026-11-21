"""Summarise HEVC (H.265) Annex B streams as JSON.

    python3 hevcinfo.py clip.h265 [more files ...]
"""
import json
import sys

from hevckit.hevc import HevcError, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (OSError, HevcError, ValueError, IndexError, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
