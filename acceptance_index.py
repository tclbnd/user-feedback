"""Acceptance 3: incremental index maintenance + reproducible export."""
import json
import sys

from annotations import ANN
from lineage_index import LineageIndex
from acceptance_static import SCHEMA


def load_queries():
    return dict(("q%02d" % i, open("queries/q%02d.sql" % i).read())
                for i in range(1, 33))


def main():
    failures = []
    idx = LineageIndex(SCHEMA)
    queries = load_queries()
    for qid, sql in sorted(queries.items()):
        idx.set_query(qid, sql)

    # 1. single query change -> only that node is recomputed
    new_q03 = queries["q03"].replace(
        "SELECT o.id, c.name, o.amount",
        "SELECT o.id, c.name, o.amount, o.status")
    recomputed = idx.set_query("q03", new_q03)
    if recomputed != ["q03"]:
        failures.append("query change recomputed %s, want ['q03']"
                        % recomputed)
    dm = idx.analyses["q03"].dep_map()
    if dm["status"]["value"] != ["orders.status"]:
        failures.append("q03 re-analysis wrong: %s" % dm.get("status"))
    idx.set_query("q03", queries["q03"])  # restore

    # 2. schema change -> exactly the queries referencing the table
    recomputed = idx.update_schema(
        "customers", SCHEMA["customers"] + ["loyalty"])
    want = sorted(qid for qid, sql in queries.items()
                  if "customers" in sql)
    if recomputed != want:
        failures.append("schema change recomputed %s, want %s"
                        % (recomputed, want))
    # new column must be visible: star query q06 now exposes it
    if "loyalty" not in idx.analyses["q06"].dep_map():
        failures.append("q06 did not pick up new column loyalty")
    idx.update_schema("customers", SCHEMA["customers"])  # restore

    # 3. impact of a source column change, with evidence paths
    changes = [dict(table="orders", col="amount", kind="column")]
    affected = idx.impact(changes)
    # every affected output must list orders.amount in its deps
    for qid, cols in affected.items():
        dm = idx.analyses[qid].dep_map()
        for col, info in cols.items():
            deps = dm[col]
            ok = ("orders.amount" in deps["value"]
                  or "orders.amount" in deps["row"])
            if not ok:
                failures.append("%s.%s impacted but orders.amount not in "
                                "deps" % (qid, col))
            if not info["evidence"]:
                failures.append("%s.%s has no evidence path" % (qid, col))
            for ev in info["evidence"]:
                if not (ev["line"] >= 0 and ev["col"] >= 0 and ev["scope"]):
                    failures.append("%s.%s evidence lacks position: %s"
                                    % (qid, col, ev))
    # spot check: q01 does not touch orders -> must be unaffected
    if "q01" in affected:
        failures.append("q01 wrongly affected by orders.amount change")
    # q23: amount is a row dep (ORDER BY+LIMIT) -> row impact present
    if "row" not in affected.get("q23", {}).get("id", {}).get("kinds", []):
        failures.append("q23.id should be row-affected by orders.amount")

    # 4. reproducible export: two runs produce byte-identical files
    t1 = idx.export_impact(changes, "out/impact_orders_amount.json")
    t2 = idx.export_impact(changes, "out/impact_orders_amount.json")
    if t1 != t2:
        failures.append("impact export is not reproducible")
    g1 = idx.export_graph("out/lineage_graph.json")
    g2 = idx.export_graph("out/lineage_graph.json")
    if g1 != g2:
        failures.append("graph export is not reproducible")
    doc = json.loads(t1)
    if not doc["manifest"]["schema_sha256"] \
            or len(doc["manifest"]["query_sha256"]) != 32:
        failures.append("manifest incomplete")

    if failures:
        print("INDEX ACCEPTANCE: FAIL")
        for f in failures:
            print(" -", f)
        return 1
    print("INDEX ACCEPTANCE: PASS (single-query recompute, schema-change "
          "recompute of %d queries, evidence paths, byte-identical "
          "re-exports)" % len(want))
    return 0


if __name__ == "__main__":
    sys.exit(main())
