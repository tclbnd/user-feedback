"""Acceptance 2: dynamic validation against independent SQLite execution.

For controlled per-column value mutations and row insert/delete mutations
on synthetic data:
  * every observed change must be explainable by the lineage graph
    (observed subset of predicted impact; row-count changes require a
    predicted row-level dependency);
  * pre-designed irrelevant mutations must yield empty predicted AND
    empty observed recompute sets.
"""
import json
import sqlite3
import sys

from acceptance_static import SCHEMA
from annotations import ANN
from lineage_index import LineageIndex
import seed

EXEC_QIDS = sorted(qid for qid in ANN if ANN[qid]["executable"])


def run_all(conn, queries):
    out = {}
    for qid in EXEC_QIDS:
        cur = conn.execute(queries[qid])
        cols = [d[0] for d in cur.description]
        rows = sorted((tuple(repr(v) for v in row) for row in cur.fetchall()))
        out[qid] = (cols, rows)
    return out


def observed_changes(base, after):
    """{qid: {col: need}} where need is 'row' (membership changed) or
    'any' (value multiset changed)."""
    changes = {}
    for qid in base:
        bcols, brows = base[qid]
        acols, arows = after[qid]
        ch = {}
        if len(brows) != len(arows):
            for c in bcols:
                ch[c] = "row"
        else:
            for i, c in enumerate(bcols):
                bvals = sorted(r[i] for r in brows)
                avals = sorted(r[i] for r in arows)
                if bvals != avals:
                    ch[c] = "any"
        if ch:
            changes[qid] = ch
    return changes


def main():
    queries = dict(("q%02d" % i, open("queries/q%02d.sql" % i).read())
                   for i in range(1, 33))
    idx = LineageIndex(SCHEMA)
    for qid, sql in sorted(queries.items()):
        idx.set_query(qid, sql)

    conn = sqlite3.connect(":memory:")
    seed.build(conn)
    baseline = run_all(conn, queries)

    failures = []
    stats = dict(mutations=0, unexplained=0, irrelevant_ok=0,
                 pred_cells=0, obs_cells=0)
    run_log = []

    mutations = []  # (label, table, col|None, sql, designed_irrelevant)
    for table in sorted(seed.INT_COLS):
        for col in seed.INT_COLS[table] + seed.TEXT_COLS[table]:
            mutations.append(
                ("%s.%s" % (table, col), table, col,
                 seed.column_mutation_sql(table, col),
                 (table, col) in seed.IRRELEVANT_COLS))
    for table, sql in seed.ROW_MUTATIONS:
        mutations.append(("%s(rows)" % table, table, None, sql,
                        table in seed.IRRELEVANT_ROWS))

    for label, table, col, sql, irrelevant in mutations:
        stats["mutations"] += 1
        conn.execute("SAVEPOINT m")
        conn.execute(sql)
        after = run_all(conn, queries)
        conn.execute("ROLLBACK TO m")
        conn.execute("RELEASE m")

        change = dict(table=table, col=col,
                      kind="rows" if col is None else "column")
        predicted = idx.impact([change])  # {qid: {col: {kinds}}}
        observed = observed_changes(baseline, after)

        # soundness: every observed change must be explainable
        for qid, cols in observed.items():
            pred_cols = predicted.get(qid, {})
            for c, need in cols.items():
                info = pred_cols.get(c)
                if info is None:
                    failures.append(
                        "%s: observed change in %s.%s not explained by "
                        "lineage" % (label, qid, c))
                    stats["unexplained"] += 1
                elif need == "row" and "row" not in info["kinds"]:
                    failures.append(
                        "%s: row-membership change in %s.%s but lineage "
                        "has only kinds=%s" % (label, qid, c, info["kinds"]))
                    stats["unexplained"] += 1

        stats["pred_cells"] += sum(
            len(c) for qid, c in predicted.items() if qid in EXEC_QIDS)
        stats["obs_cells"] += sum(len(c) for c in observed.values())

        run_log.append(dict(
            mutation=label, table=table, col=col,
            kind="rows" if col is None else "column",
            designed_irrelevant=irrelevant,
            predicted=sorted((qid, c) for qid, cols in predicted.items()
                             if qid in EXEC_QIDS for c in cols),
            observed=sorted((qid, c) for qid, cols in observed.items()
                            for c in cols)))

        # designed-irrelevant mutations: both sets must be exactly empty
        if irrelevant:
            pred_exec = dict((qid, c) for qid, c in predicted.items()
                             if qid in EXEC_QIDS)
            if pred_exec or observed:
                failures.append(
                    "%s: designed-irrelevant mutation but predicted=%s "
                    "observed=%s" % (label, sorted(pred_exec),
                                     sorted(observed)))
            else:
                stats["irrelevant_ok"] += 1

    print("ran %d controlled mutations x %d executable queries"
          % (stats["mutations"], len(EXEC_QIDS)))
    print("irrelevant mutations verified empty: %d"
          % stats["irrelevant_ok"])
    print("impact cells predicted=%d, observed=%d (prediction is a "
          "conservative superset)" % (stats["pred_cells"],
                                      stats["obs_cells"]))
    doc = dict(
        manifest=idx.manifest(),
        mutations=run_log,
        stats=dict((k, stats[k]) for k in sorted(stats)),
        failures=failures,
    )
    open("out/dynamic_validation.json", "w").write(
        json.dumps(doc, indent=1, sort_keys=True) + "\n")

    if failures:
        print("DYNAMIC ACCEPTANCE: FAIL (%d problems)" % len(failures))
        for f in failures[:40]:
            print(" -", f)
        return 1
    print("DYNAMIC ACCEPTANCE: PASS (every observed change is explained "
          "by lineage; irrelevant mutations recompute nothing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
