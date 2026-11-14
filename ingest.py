import json
import sys
from pathlib import Path


def main(input_path, state_path):
    request = json.loads(Path(input_path).read_text())
    rows = [row for batch in request["batches"] for row in batch]
    totals = {}
    for row in rows:
        day = totals.setdefault(row["day"], {})
        device = day.setdefault(row["device"], {"units": 0, "count": 0})
        device["units"] += row["units"]
        device["count"] += 1
    result = {"rows": rows, "totals": totals, "changes": [], "seen": rows}
    Path(state_path).write_text(json.dumps(result) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
