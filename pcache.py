import json
import sys
from pathlib import Path


def evaluate(row, rules):
    for rule in rules:
        if all(str(row.get(term.split("=", 1)[0])) == term.split("=", 1)[1]
               for term in rule["pred"] if "=" in term):
            return rule["allow"]
    return False


def main(input_path, output_path):
    data = json.loads(Path(input_path).read_text())
    rows = [query["sub"] for query in data["queries"]]
    results = []
    for change in data["changes"]:
        if change["kind"] == "attr":
            rows[change["sub_i"]][change["key"]] = change["to"]
        results.extend(evaluate(row, data["rules"]) for row in rows)
    result = dict(results=results, hits=0, misses=len(results),
                  invalidations={}, dep_index={})
    Path(output_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main(*sys.argv[1:])
