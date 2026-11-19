"""Run a MERGE against a target table.

    python3 runmerge.py job.json result.json

job.json:

    {"target": {"name": "t", "key": "id", "columns": [...], "unique": [...], "rows": [...]},
     "source": [...rows...],
     "merge": {"on": ["id", "id"], "when": [...]}}

``merge`` may be omitted to use the daily customer sync statement (then the
target must be the customer dimension, and the result also lists the
``dim_customer_history`` rows; existing ones can be given as ``history``).  The result has the statement
outcome and the target rows after the transaction ends.
"""
import json
import sys

from mergeq.dimsync import DAILY_MERGE, run_sync
from mergeq.history import history_table
from mergeq.table import Table


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    spec = job["target"]
    table = Table(spec.get("name", "t"), spec["key"], spec["columns"], spec.get("unique", ()))
    for row in spec.get("rows", []):
        table.insert(row)
    history = history_table(job.get("history", [])) if "merge" not in job else None
    outcome = run_sync(table, job["source"], job.get("merge", DAILY_MERGE), history)
    out = {"outcome": outcome, "rows": table.dump()}
    if history is not None:
        out["history"] = history.dump()
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
