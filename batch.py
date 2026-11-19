"""Print the scenario for a settlement batch next to other sessions.

    python3 batch.py records.json scenario.json

records.json: {"table": "accounts", "records": [["1", 90], ["2", 40]],
               "work": 2, "statement_timeout": 4,
               "others": {"app": {"script": [...]}}, "cancel": [...], "data": {...}}
"""
import json
import sys

from cancel.batch import settlement_script


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    batch = {"script": settlement_script(spec["table"], [tuple(r) for r in spec["records"]], spec.get("work", 1))}
    if spec.get("statement_timeout"):
        batch["statement_timeout"] = spec["statement_timeout"]
    scenario = {"data": spec.get("data", {}), "sessions": dict(spec.get("others", {}), batch=batch),
                "cancel": spec.get("cancel", [])}
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(scenario, f, indent=1)


if __name__ == "__main__":
    main()
