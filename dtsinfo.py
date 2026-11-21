"""Summarise DTS core streams as JSON (raw .dts files or DTS-in-WAV images).

    python3 dtsinfo.py film.dts [more files ...]
"""
import json
import sys

from dtskit.dts import DtsError, scan


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **scan(fh.read())})
        except (OSError, DtsError, ValueError, IndexError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
