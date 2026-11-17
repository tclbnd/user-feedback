#!/usr/bin/env python3
"""End-to-end acceptance check: migrate a wide table into the 3NF schema and
reconstruct it by natural joins, asserting row-by-row equality.

    python3 verify_join.py <spec.json> <sample.csv> [<outdir>]

The original table and every generated relation are loaded into an in-memory
SQLite database, schema.sql + migrate.sql are executed, and the relations are
joined back on their shared columns. The joined rows are compared to the
original sample as ordered tuples in the schema's column order. Prints a JSON
report and exits 0 on success, 1 on mismatch.
"""
import csv
import json
import os
import sqlite3
import sys

from design import analyze, sql, violations
from fd import minimal_cover


def load_spec(path):
    spec = json.load(open(path, encoding="utf-8"))
    return spec, [(f["lhs"], f["rhs"]) for f in spec["fds"]]


def load_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def build_database(spec, rows, report, outdir=None):
    ddl, dml = sql(spec, report)
    if outdir is not None:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "schema.sql"), "w", encoding="utf-8") as fh:
            fh.write(ddl)
        with open(os.path.join(outdir, "migrate.sql"), "w", encoding="utf-8") as fh:
            fh.write(dml)

    con = sqlite3.connect(":memory:")
    cur = con.cursor()
    cols = ", ".join(f"{a} TEXT" for a in spec["attributes"])
    cur.execute(f"CREATE TABLE {spec['table']} ({cols});")
    placeholders = ", ".join("?" for _ in spec["attributes"])
    cur.executemany(
        f"INSERT INTO {spec['table']} VALUES ({placeholders});",
        [tuple(r[a] for a in spec["attributes"]) for r in rows])
    cur.executescript(ddl)
    cur.executescript(dml)
    return con


def natural_join_sql(spec, report):
    """Chain NATURAL JOINs; every split is lossless so the chain reconnects."""
    names = [r["name"] for r in report["relations"]]
    cols = ", ".join(spec["attributes"])
    sql_text = f"SELECT {cols} FROM {names[0]}"
    for name in names[1:]:
        sql_text += f" NATURAL JOIN {name}"
    return sql_text + ";"


def verify(spec_path, csv_path, outdir=None):
    spec, fds = load_spec(spec_path)
    rows = load_rows(csv_path)

    bad = violations(spec, rows)
    if bad:
        return {"ok": False, "stage": "dependency_check", "violations": bad}, 3

    report = analyze(spec, minimal_cover(fds, spec["attributes"]))
    con = build_database(spec, rows, report, outdir)
    cur = con.cursor()

    original = [tuple(r[a] for a in spec["attributes"]) for r in rows]
    # SQLite applies type affinity (INTEGER columns come back as int); compare
    # canonical text values so the check is about data, not Python types.
    joined = [tuple("" if v is None else str(v)
                    for v in row)
              for row in cur.execute(natural_join_sql(spec, report)).fetchall()]

    def canon(seq):
        return sorted(seq)

    row_counts = {
        r["name"]: cur.execute(f"SELECT COUNT(*) FROM {r['name']};").fetchone()[0]
        for r in report["relations"]}
    result = {
        "ok": canon(joined) == canon(original),
        "stage": "natural_join",
        "original_rows": len(original),
        "joined_rows": len(joined),
        "relation_rows": row_counts,
        "is_bcnf": report["is_bcnf"],
        "lost_dependencies": report["lost_dependencies"],
    }
    if not result["ok"]:
        missing = [list(r) for r in canon(original) if r not in set(joined)]
        extra = [list(r) for r in canon(joined) if r not in set(original)]
        result["missing_rows"] = missing[:5]
        result["extra_rows"] = extra[:5]
        return result, 1
    return result, 0


def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    outdir = argv[3] if len(argv) == 4 else None
    report, code = verify(argv[1], argv[2], outdir)
    print(json.dumps(report, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
