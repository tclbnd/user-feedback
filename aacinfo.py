"""Summarise ADTS files as JSON.

    python3 aacinfo.py capture/0800.aac capture/0900.aac
"""
import json
import sys

from aacpack.adts import AdtsError
from aacpack.probe import probe


def main(paths):
    out = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                out.append({"file": path, **probe(fh.read())})
        except (AdtsError, OSError) as exc:
            out.append({"file": path, "error": str(exc)})
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
