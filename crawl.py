#!/usr/bin/env python3
"""Initial local importer for captured rows."""

import argparse
import json
import os
from pathlib import Path


def run(sim, state=None, fail_at=None):
    rules = {spec["source"]: spec["rule"] for spec in sim["initial_rules"]}
    rows = {}
    cursor = {}
    audit = []
    for event in sorted(sim["events"], key=lambda item: item["id"]):
        if event["kind"] != "row":
            continue
        source, partition = event["source"], event["partition"]
        key = source, partition, event["key"]
        rule = rules.get(source)
        if event["op"] == "delete":
            rows.pop(key, None)
        elif rule:
            try:
                amount = event["data"][rule["amount_field"]] * rule["multiplier"]
                status = rule["enum"][event["data"][rule["enum_field"]]]
            except (KeyError, TypeError):
                continue
            rows[key] = {
                "source": source, "partition": partition, "key": event["key"],
                "revision": event["revision"], "amount_cents": amount, "status": status,
            }
        if fail_at == f"after-row:{event['id']}":
            os._exit(75)
        cursor[f"{source}/{partition}"] = event["seq"]
        audit.append({"id": event["id"], "action": "accepted"})
        if fail_at == f"before-commit:{event['id']}":
            os._exit(75)
        if state is not None:
            state.write_text(json.dumps(cursor), encoding="utf-8")
        if fail_at == f"before-ack:{event['id']}":
            os._exit(75)
    return {
        "rows": [rows[key] for key in sorted(rows)], "tombstones": [],
        "quarantine": [], "conflicts": [], "cursor": cursor, "backfilled": [], "audit": audit,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("simulation", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--fail-at")
    args = parser.parse_args()
    sim = json.loads(args.simulation.read_text(encoding="utf-8"))
    report = run(sim, args.state, args.fail_at)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
