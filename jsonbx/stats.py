"""Index size report for the capacity review."""


def index_report(table):
    out = []
    for name, ix in sorted(table.indexes.items()):
        out.append({"index": name, "items": len(ix.entries), "postings": ix.size(), "rows": len(ix.rows)})
    return out
