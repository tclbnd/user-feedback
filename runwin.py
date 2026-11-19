"""Run window function calls over a table.

    python3 runwin.py table.json calls.json result.json

table.json: {"rows": [{"id": 1, ...}, ...]}
calls.json: [{"fn": "sum", "arg": "amount", "window": {...}}, ...]   (see winx/frames.py)
The result has, for every call, the value per row id.
"""
import json
import sys

from winx.engine import evaluate


def main():
    rows = json.load(open(sys.argv[1], encoding="utf-8"))["rows"]
    calls = json.load(open(sys.argv[2], encoding="utf-8"))
    out = [{"call": c, "values": {str(k): v for k, v in sorted(evaluate(rows, c).items())}} for c in calls]
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
