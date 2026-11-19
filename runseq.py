"""Replay sequence operations from several sessions.

    python3 runseq.py script.json result.json

script.json: {"sequences": {"s": {"increment": 1, "start": 100, "cache": 1, ...}},
              "ops": [["A", "nextval", "s"], ["B", "currval", "s"], ["A", "setval", "s", 50, false],
                      ["A", "lastval"], ["A", "begin"], ["A", "rollback"], ["A", "commit"]]}

Every op reports its value or the error message.
"""
import json
import sys

from seqdb.catalog import Catalog
from seqdb.sequence import SequenceError
from seqdb.session import Session


def run(spec):
    catalog = Catalog()
    for name, opts in spec.get("sequences", {}).items():
        catalog.create(name, **opts)
    sessions, out = {}, []
    for op in spec["ops"]:
        who, cmd, args = op[0], op[1], op[2:]
        s = sessions.setdefault(who, Session(who, catalog))
        try:
            value = getattr(s, cmd)(*args)
            out.append({"op": op, "value": value})
        except SequenceError as exc:
            out.append({"op": op, "error": str(exc)})
    return {"results": out}


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(spec), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
