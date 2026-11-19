"""Index size estimates for the capacity report."""

ENTRY_BYTES = 24


def estimate(indexes, total_rows):
    out = []
    for ix in indexes:
        out.append({"index": ix.name, "rows": ix.size(), "bytes": ix.size() * ENTRY_BYTES,
                    "saved_vs_full": (total_rows - ix.size()) * ENTRY_BYTES})
    return out
