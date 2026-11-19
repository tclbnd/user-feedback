"""Load a CSV file the way COPY FROM ... WITH (FORMAT csv) does.

    python3 copyin.py job.json data.csv result.json

job.json: {"table": {"columns": [...]}, "options": {"header": true, "null": "", ...}}
The result has the loaded rows, or the error that aborted the COPY.
"""
import json
import sys

from csvcopy.loader import CopyError, copy_from


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    data = open(sys.argv[2], encoding="utf-8", newline="").read()
    try:
        out = {"rows": copy_from(job["table"], data, job.get("options"))}
    except CopyError as exc:
        out = {"error": str(exc)}
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
