"""Run a join.

    python3 runjoin.py job.json result.json [--nested-loop]

job.json: {"left": [{"id": "l1", "k": 1}, ...], "right": [...], "lkey": "k",
           "rkey": "k", "kind": "inner"}

``lkey``/``rkey`` may be lists of columns (composite key); ``filter`` is an
optional list of extra join conditions, see mjoin/keys.py.
"""
import json
import sys

from mjoin.explain import explain
from mjoin.planner import join


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    result = join(job["left"], job["right"], job.get("lkey", "k"), job.get("rkey", "k"), job.get("kind", "inner"),
                  sorted_inputs="--nested-loop" not in sys.argv, filters=job.get("filter"))
    result["rows"] = sorted(result["rows"], key=lambda t: (str(t[0]), str(t[1])))
    result["explain"] = explain(result, job.get("kind", "inner"), job.get("lkey", "k"), job.get("rkey", "k"),
                                job.get("filter"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)


if __name__ == "__main__":
    main()
