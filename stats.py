"""ANALYZE: sample a column and build null fraction, distinct count, MCV list and histogram."""
from collections import Counter
from math import ceil

SAMPLE_ROWS, MCV_LIMIT, BUCKETS = 3000, 10, 10


def sample(values):
    step = max(1, ceil(len(values) / SAMPLE_ROWS))
    return values[::step]


def analyze_column(values):
    rows = sample(values)
    n = len(rows)
    present = [v for v in rows if v is not None]
    null_frac = (n - len(present)) / n if n else 0.0
    counts = Counter(present)
    ndv = len(set(rows))
    mcv = []
    if ndv:
        average = len(present) / ndv
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        mcv = [[v, c / len(present)] for v, c in ranked[:MCV_LIMIT]
               if c >= 2 and (ndv <= MCV_LIMIT or c > 1.25 * average)]
    rest = sorted(present)
    if len(rest) >= 2:
        hist = [rest[round(i * (len(rest) - 1) / BUCKETS)] for i in range(BUCKETS + 1)]
    else:
        hist = rest
    return {"null_frac": null_frac, "ndv": ndv, "mcv": mcv, "histogram": hist}


def analyze(table):
    columns = table["columns"]
    count = len(next(iter(columns.values()))) if columns else 0
    return {"rows": count, "columns": {c: analyze_column(v) for c, v in columns.items()}}
