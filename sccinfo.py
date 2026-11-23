"""Summarise Scenarist SCC caption files as JSON.

    python3 sccinfo.py episode.scc [more files ...]
"""
import json
import sys

from scckit.scc import SccError, parse


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, encoding="latin-1") as fh:
                out.append({"file": path, **parse(fh.read())})
        except (OSError, SccError, ValueError, IndexError, KeyError) as exc:
            out.append({"file": path, "error": str(exc)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main(sys.argv[1:])
