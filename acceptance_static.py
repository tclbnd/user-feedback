"""Acceptance 1: static lineage vs 32 hand-annotated queries.

Compares, per query and per output column, the dependency SET and TYPE
(value/row) against annotations, and checks that ambiguity / missing
schema / unsupported syntax reports fire at the exact annotated position.
"""
import json
import sys

from analyzer import Analyzer
from annotations import ANN

SCHEMA = dict(
    customers=["id", "name", "region", "segment", "created_at"],
    orders=["id", "customer_id", "status", "amount", "ordered_at"],
    order_items=["order_id", "product_id", "qty", "unit_price", "discount"],
    products=["id", "sku", "name", "category", "price", "active"],
    shipments=["id", "order_id", "shipped_at", "carrier", "status"],
    events=["id", "customer_id", "event_type", "payload", "ts"],
)


def main():
    an = Analyzer(SCHEMA)
    failures = []
    checked_cols = 0
    for i in range(1, 33):
        qid = "q%02d" % i
        sql = open("queries/%s.sql" % qid).read()
        a = an.analyze(qid, sql)
        exp = ANN[qid]
        got = a.dep_map()
        want = exp["outputs"]
        if sorted(got) != sorted(want):
            failures.append(
                "%s: output columns differ: got %s want %s"
                % (qid, sorted(got), sorted(want)))
            continue
        for name in sorted(want):
            checked_cols += 1
            for kind in ("value", "row"):
                g, w = got[name][kind], want[name][kind]
                if g != w:
                    failures.append(
                        "%s.%s [%s]:\n    got  %s\n    want %s"
                        % (qid, name, kind, g, w))
        # reports: kinds must match exactly; annotated positions must match
        got_reps = [(r["kind"], r["line"], r["col"]) for r in a.reports]
        want_reps = exp["reports"]
        if len(got_reps) != len(want_reps):
            failures.append("%s: reports differ: got %s want %d report(s)"
                            % (qid, got_reps, len(want_reps)))
        else:
            for (gk, gl, gc), wr in zip(got_reps, want_reps):
                if gk != wr["kind"]:
                    failures.append("%s: report kind got %s want %s"
                                    % (qid, gk, wr["kind"]))
                elif "line" in wr and (gl, gc) != (wr["line"], wr["col"]):
                    failures.append(
                        "%s: report %s at %d:%d, want %d:%d"
                        % (qid, gk, gl, gc, wr["line"], wr["col"]))
    print("checked 32 queries, %d annotated output columns, "
          "%d report expectations" % (checked_cols, sum(
              len(ANN["q%02d" % i]["reports"]) for i in range(1, 33))))
    if failures:
        print("STATIC ACCEPTANCE: FAIL (%d problems)" % len(failures))
        for f in failures:
            print(" -", f)
        return 1
    print("STATIC ACCEPTANCE: PASS (dependency sets, types and report "
          "positions all match the annotations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
