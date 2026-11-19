"""Nightly HR jobs.

    python3 hrjob.py export org.json out.csv          reporting lines below the CEO
    python3 hrjob.py review org.json <manager> out.json   people below a manager, once each
"""
import json
import sys

from rcte.export import org_rows, people_under, to_csv
from rcte.graph import load
from rcte.metrics import slow_log


def main():
    action, graph_file = sys.argv[1], sys.argv[2]
    graph = load(json.load(open(graph_file, encoding="utf-8")))
    if action == "export":
        with open(sys.argv[3], "w", encoding="utf-8") as f:
            f.write(to_csv(org_rows(graph)))
    elif action == "review":
        with open(sys.argv[4], "w", encoding="utf-8") as f:
            json.dump({"manager": sys.argv[3], "people": people_under(graph, sys.argv[3])}, f, indent=1)
    else:
        raise SystemExit(f"unknown action {action}")
    for line in slow_log(graph.stats):
        print(line, file=sys.stderr)


if __name__ == "__main__":
    main()
