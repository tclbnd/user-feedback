"""Replay index operations and queries.

    python3 run.py ops.json result.json

ops.json is a list of operations:

    {"op": "add" | "update", "doc": "a1", "text": "..."}
    {"op": "delete", "doc": "a1"}
    {"op": "flush"}            {"op": "merge"}          merge all segments
    {"op": "merge_tier"}       merge the smallest segments (size-tiered)
    {"op": "merge_begin"}      start a background tiered merge
    {"op": "merge_commit"}     install the merge started by merge_begin
    {"op": "search", "q": "brown fox"}   all words, any position
    {"op": "phrase", "q": "brown fox"}   words adjacent and in this order

The result lists the answer of every search/phrase operation and the index
stats at the end.
"""
import json
import sys

from ftsearch.index import InvertedIndex
from ftsearch.merge import BackgroundMerge, merge_segments, merge_tier, pick_tier


def run(ops):
    index = InvertedIndex()
    answers = []
    pending = None
    for op in ops:
        kind = op["op"]
        if kind == "add":
            index.add(op["doc"], op["text"])
        elif kind == "update":
            index.update(op["doc"], op["text"])
        elif kind == "delete":
            index.delete(op["doc"])
        elif kind == "flush":
            index.flush()
        elif kind == "merge":
            merge_segments(index)
        elif kind == "merge_tier":
            merge_tier(index)
        elif kind == "merge_begin":
            pending = BackgroundMerge(index, pick_tier(index)).begin()
        elif kind == "merge_commit":
            if pending is not None:
                pending.commit()
                pending = None
        elif kind == "search":
            answers.append({"q": op["q"], "docs": index.search(op["q"])})
        elif kind == "phrase":
            answers.append({"q": op["q"], "phrase": True, "docs": index.phrase(op["q"])})
        else:
            raise ValueError(kind)
    return {"answers": answers, "stats": index.stats()}


def main():
    ops = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(run(ops), f, indent=1)


if __name__ == "__main__":
    main()
