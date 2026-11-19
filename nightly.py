"""Nightly customer sync for a large extract.

    python3 nightly.py job.json result.json [chunk_rows]

job.json has the same ``target`` and ``source`` as for runmerge.py, plus an
optional ``history`` list with the existing history rows.
"""
import json
import sys

from mergeq.dimsync import customer_table
from mergeq.history import history_table
from mergeq.nightly import CHUNK_ROWS, run_nightly


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    chunk = int(sys.argv[3]) if len(sys.argv) > 3 else CHUNK_ROWS
    table = customer_table(job["target"].get("rows", []))
    history = history_table(job.get("history", []))
    outcome = run_nightly(table, job["source"], history, chunk_rows=chunk)
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump({"outcome": outcome, "rows": table.dump(), "history": history.dump()},
                  f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
