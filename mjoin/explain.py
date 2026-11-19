"""EXPLAIN ANALYZE-style text for the join node."""
from .keys import describe


def explain(result, kind, lkey, rkey, filters=None):
    lines = [f"{result['method'].title()}  ({kind})  cond: {describe(lkey, rkey, filters)}", f"  rows={len(result['rows'])}"]
    if result.get("fetches") is not None:
        lines.append(f"  input fetches={result['fetches']}")
    return "\n".join(lines)
