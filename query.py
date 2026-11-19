"""Run JSON path predicates with and without the index.

    python3 query.py docs.json predicates.json result.json

docs.json: {"p1": {...}, "p2": {...}}
predicates.json: [{"path": "$.tags[*]", "op": "=", "value": "red"}, ...]

The predicate list may also contain write operations, applied in order
between the queries: {"put": "p3", "doc": {...}} replaces a document and
{"delete": "p3"} removes one.
"""
import json
import sys

from jsonidx.table import DocTable


def main():
    docs = json.load(open(sys.argv[1], encoding="utf-8"))
    preds = json.load(open(sys.argv[2], encoding="utf-8"))
    table = DocTable()
    for doc_id, doc in docs.items():
        table.insert(doc_id, doc)
    out = []
    for pred in preds:
        if "put" in pred:
            table.replace(pred["put"], pred["doc"])
            continue
        if "delete" in pred:
            table.delete(pred["delete"])
            continue
        q = table.query(pred)
        out.append({"pred": pred, "plan": q["plan"], "index": q["ids"], "scan": table.scan(pred)})
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
