"""Streaming session replay utility."""
import argparse
import json
from pathlib import Path


def inspect(path):
    if path.exists():
        return json.loads(path.read_text())
    return {"processed": 0, "watermark": None, "rows": [], "batches": [], "late": []}


def apply(source, state, limit=None, crash=False):
    result = inspect(state)
    last = {}
    rows = {(r["key"], r["start"]): r for r in result["rows"]}
    for number, event in enumerate(source["events"][:limit], 1):
        result["processed"] = number
        if event["kind"] == "watermark":
            result["watermark"] = event["t"]
        if event["kind"] != "event":
            continue
        key, t = event["key"], event["t"]
        if key not in last or t - last[key]["end"] > source["gap"]:
            last[key] = {"key": key, "start": t, "end": t, "event_ids": []}
        row = last[key]
        row["end"] = max(t, row["end"])
        row["event_ids"].append(event["id"])
        rows[key, row["start"]] = row
    # This baseline publishes every arrival and never retracts previous results.
    result["rows"] = list(rows.values())
    state.write_text(json.dumps(result) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("source", type=Path)
    apply_parser.add_argument("state", type=Path)
    apply_parser.add_argument("--limit", type=int)
    apply_parser.add_argument("--crash-during-merge", action="store_true")
    view = sub.add_parser("inspect")
    view.add_argument("state", type=Path)
    args = parser.parse_args()
    if args.command == "apply":
        data = json.loads(args.source.read_text())
        result = apply(data, args.state, args.limit, args.crash_during_merge)
    else:
        result = inspect(args.state)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
