"""Small replica driver over an exported snapshot and a synthetic change feed."""
import argparse
import json
from pathlib import Path


def open_source(path):
    return json.loads(path.read_text())


def apply_ops(tables, ops):
    for item in ops:
        table = tables.setdefault(item["table"], {})
        key = item["key"]
        if item["action"] == "set":
            table[key] = item["value"]
        elif item["action"] == "delete":
            table.pop(key, None)
        elif item["action"] == "move":
            value = table.pop(key)
            table[item["new_key"]] = {**value, **item.get("patch", {})}
        else:
            raise ValueError("unknown action")


def inspect(db):
    if db.exists():
        return json.loads(db.read_text())
    return {"published": False, "tables": {}, "audit": [], "visible_lsn": None, "applied_events": 0}


def apply(source, db, limit, crash):
    # Existing batch import keeps no durable partial progress. It ignores
    # changes received before the snapshot is marked complete.
    result = inspect(db)
    active = {}
    for event in source["events"][:limit]:
        kind = event["kind"]
        if kind == "seal":
            for chunk in source["chunks"]:
                result["tables"].setdefault(chunk["table"], {}).update(chunk["rows"])
            result["published"] = True
            result["visible_lsn"] = source["snapshot_lsn"]
        elif result["published"] and kind == "begin":
            active[event["xid"]] = []
        elif result["published"] and kind == "op" and event["xid"] in active:
            active[event["xid"]].append(event)
        elif result["published"] and kind == "abort":
            active.pop(event["xid"], None)
        elif result["published"] and kind == "commit" and event["xid"] in active:
            apply_ops(result["tables"], active.pop(event["xid"]))
            result["visible_lsn"] = event["lsn"]
            result["audit"].append({"xid": event["xid"], "lsn": event["lsn"]})
        result["applied_events"] += 1
    db.write_text(json.dumps(result) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("apply")
    sync.add_argument("--source", type=Path, required=True)
    sync.add_argument("--db", type=Path, required=True)
    sync.add_argument("--limit", type=int)
    sync.add_argument("--crash", choices=["after-stage", "before-publish", "before-commit", "after-commit"])
    view = commands.add_parser("inspect")
    view.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "apply":
            result = apply(open_source(args.source), args.db, args.limit, args.crash)
        else:
            result = inspect(args.db)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
