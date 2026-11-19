"""Differential tests: the evaluator and the flattener must agree with SQL.

SQLite is used as the reference engine.  It shares the SQL three-valued
logic that PostgreSQL implements for the constructs under test: NULL
propagation, ``IN`` / ``NOT IN`` subqueries, ``EXISTS``, ``ANY`` / ``ALL``
and row-wise comparison.  For every generated query we check that

  * row-by-row evaluation, and
  * the flattened semi/anti-join plan

return exactly the rows SQLite returns, for many random NULL distributions.
"""
import itertools
import random
import sqlite3
import unittest

from subq.expr import evaluate, filter_rows
from subq.rewrite import run

VALUES = [None, 1, 2, 3]


def _dbs():
    """A handful of small databases covering awkward NULL placements."""
    return [
        {
            "t": [
                {"id": 1, "a": 1, "b": 1},
                {"id": 2, "a": 1, "b": None},
                {"id": 3, "a": None, "b": 2},
                {"id": 4, "a": 2, "b": None},
                {"id": 5, "a": None, "b": None},
                {"id": 6, "a": 3, "b": 2},
            ],
            "s": [
                {"k1": 1, "k2": 1},
                {"k1": 2, "k2": None},
                {"k1": None, "k2": 2},
                {"k1": None, "k2": None},
                {"k1": 3, "k2": 2},
            ],
        },
    ]


def _sqlite(db):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER, a INTEGER, b INTEGER)")
    conn.execute("CREATE TABLE s (k1 INTEGER, k2 INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?, ?, ?)",
                     [(r["id"], r["a"], r["b"]) for r in db["t"]])
    conn.executemany("INSERT INTO s VALUES (?, ?)",
                     [(r["k1"], r["k2"]) for r in db["s"]])
    conn.commit()
    return conn


def _sqlite_ids(conn, where_sql):
    rows = conn.execute("SELECT id FROM t WHERE " + where_sql).fetchall()
    return sorted(r[0] for r in rows)


def _ours(db, where):
    naive = sorted(r["id"] for r in filter_rows(db, "t", where))
    planned = sorted(r["id"] for r in run(db, {"from": "t", "where": where})[0])
    return naive, planned


# Each case is (predicate, equivalent SQL text).  The SQL text uses the
# same tables so SQLite can act as the oracle.
def _cases():
    cases = []

    def add(name, pred, sql):
        cases.append((name, pred, sql))

    # IN / NOT IN against a 1-column subquery, including NULL keys.
    for outer in ("a", "b"):
        for op, key in (("in", "in"), ("not_in", "not in")):
            add(
                f"{outer} {key} (select k1)",
                {op: {"left": {"col": outer}, "sub": {"from": "s", "select": ["k1"]}}},
                f"{outer} {key} (SELECT k1 FROM s)",
            )
    # IN with a literal list containing NULL (values come from the array
    # form, which is what list-IN lowers to).
    for outer in ("a", "b"):
        add(f"{outer} in (1,NULL)",
            {"any": {"left": {"col": outer}, "op": "=", "values": [1, None]}},
            f"{outer} IN (1, NULL)")
        add(f"{outer} not in (1,NULL)",
            {"not": {"any": {"left": {"col": outer}, "op": "=", "values": [1, None]}}},
            f"{outer} NOT IN (1, NULL)")
    # Correlated EXISTS / NOT EXISTS on one and two columns.
    for outer, inner in (("a", "k1"), ("b", "k2")):
        add(f"exists t.{outer}=s.{inner}",
            {"exists": {"sub": {"from": "s", "select": ["k1"], "corr": [[outer, inner]]}}},
            f"EXISTS (SELECT 1 FROM s WHERE s.{inner} = t.{outer})")
        add(f"not exists t.{outer}=s.{inner}",
            {"not_exists": {"sub": {"from": "s", "select": ["k1"], "corr": [[outer, inner]]}}},
            f"NOT EXISTS (SELECT 1 FROM s WHERE s.{inner} = t.{outer})")
    add("exists on (a,b)=(k1,k2)",
        {"exists": {"sub": {"from": "s", "select": ["k1"],
                            "corr": [["a", "k1"], ["b", "k2"]]}}},
        "EXISTS (SELECT 1 FROM s WHERE s.k1 = t.a AND s.k2 = t.b)")
    add("not exists on (a,b)=(k1,k2)",
        {"not_exists": {"sub": {"from": "s", "select": ["k1"],
                                "corr": [["a", "k1"], ["b", "k2"]]}}},
        "NOT EXISTS (SELECT 1 FROM s WHERE s.k1 = t.a AND s.k2 = t.b)")

    # Row-wise IN / NOT IN (multi-column compare).
    add("(a,b) in (select k1,k2)",
        {"in": {"left": [{"col": "a"}, {"col": "b"}],
                "sub": {"from": "s", "select": ["k1", "k2"]}}},
        "(a, b) IN (SELECT k1, k2 FROM s)")
    add("(a,b) not in (select k1,k2)",
        {"not_in": {"left": [{"col": "a"}, {"col": "b"}],
                    "sub": {"from": "s", "select": ["k1", "k2"]}}},
        "(a, b) NOT IN (SELECT k1, k2 FROM s)")

    # ANY / ALL over literal arrays.  ``x op ANY (...)`` is true when some
    # element compares true, unknown if none does but one is unknown;
    # ``x op ALL (...)`` is the forced counterpart.  Express both with
    # EXISTS / NOT EXISTS over a one-column derived table.
    for op in ("=", "<>", "<", ">", "<=", ">="):
        for vals in ("1, 2", "1, NULL", "NULL", "1, 2, 3"):
            elements = vals.split(", ")
            values = [None if v == "NULL" else int(v) for v in elements]
            forced = " UNION ALL ".join(f"SELECT {v} AS v" for v in elements)
            # A WHERE clause keeps a row only when the predicate is TRUE, so
            # express exactly that condition:
            #   x op ANY (...) is TRUE  iff some element compares TRUE
            #   x op ALL (...) is TRUE  iff no element fails to compare TRUE
            add(f"a {op} any ({vals})",
                {"any": {"left": {"col": "a"}, "op": op, "values": values}},
                f"EXISTS (SELECT 1 FROM ({forced}) WHERE (a {op} v) IS TRUE)")
            add(f"a {op} all ({vals})",
                {"all": {"left": {"col": "a"}, "op": op, "values": values}},
                f"NOT EXISTS (SELECT 1 FROM ({forced}) WHERE (a {op} v) IS NOT TRUE)")

    # Row comparisons, including NULLs in deciding and non-deciding columns.
    for op in ("=", "<>", "<", "<=", ">", ">="):
        add(f"(a,b) {op} (2,2)",
            {"cmp": {"left": [{"col": "a"}, {"col": "b"}], "op": op, "right": [2, 2]}},
            f"(a, b) {op} (2, 2)")
        add(f"(a,b) {op} (1,NULL)",
            {"cmp": {"left": [{"col": "a"}, {"col": "b"}], "op": op, "right": [1, None]}},
            f"(a, b) {op} (1, NULL)")

    # NOT wrapped around IN / EXISTS (folded into join polarity).
    add("not (a in (select k1))",
        {"not": {"in": {"left": {"col": "a"}, "sub": {"from": "s", "select": ["k1"]}}}},
        "NOT (a IN (SELECT k1 FROM s))")
    add("not (not (a in (select k1)))",
        {"not": {"not": {"in": {"left": {"col": "a"}, "sub": {"from": "s", "select": ["k1"]}}}}},
        "NOT (NOT (a IN (SELECT k1 FROM s)))")
    add("not (exists ...)",
        {"not": {"exists": {"sub": {"from": "s", "select": ["k1"], "corr": [["a", "k1"]]}}}},
        "NOT (EXISTS (SELECT 1 FROM s WHERE s.k1 = t.a))")

    # IS DISTINCT FROM.
    add("a is distinct from b",
        {"distinct_from": [{"col": "a"}, {"col": "b"}]},
        "a IS NOT b")
    add("a is not distinct from 1",
        {"not": {"distinct_from": [{"col": "a"}, 1]}},
        "a IS 1")

    # Combinations with AND / OR / NOT.
    add("and(in, not exists)",
        {"and": [
            {"in": {"left": {"col": "a"}, "sub": {"from": "s", "select": ["k1"]}}},
            {"not_exists": {"sub": {"from": "s", "select": ["k1"], "corr": [["b", "k2"]]}}},
        ]},
        "a IN (SELECT k1 FROM s) AND NOT EXISTS (SELECT 1 FROM s WHERE s.k2 = t.b)")
    add("or(not_in, (a,b)=(1,NULL))",
        {"or": [
            {"not_in": {"left": {"col": "b"}, "sub": {"from": "s", "select": ["k2"]}}},
            {"cmp": {"left": [{"col": "a"}, {"col": "b"}], "op": "=", "right": [1, None]}},
        ]},
        "b NOT IN (SELECT k2 FROM s) OR (a, b) = (1, NULL)")
    return cases


class DifferentialTests(unittest.TestCase):
    def test_against_sqlite(self):
        db = _dbs()[0]
        conn = _sqlite(db)
        for name, pred, sql in _cases():
            with self.subTest(case=name):
                expected = _sqlite_ids(conn, sql)
                naive, planned = _ours(db, pred)
                self.assertEqual(naive, expected,
                                 f"row-by-row mismatch for {name!r}: SQL {sql!r}")
                self.assertEqual(planned, expected,
                                 f"planned mismatch for {name!r}: SQL {sql!r}")

    def test_row_by_row_equals_planned_random(self):
        """Random NULL-heavy tables: both paths must agree with SQLite."""
        rng = random.Random(20240607)
        for trial in range(200):
            db = {
                "t": [{"id": i, "a": rng.choice(VALUES[:3]), "b": rng.choice(VALUES[:3])}
                      for i in range(1, rng.randint(2, 7))],
                "s": [{"k1": rng.choice(VALUES[:3]), "k2": rng.choice(VALUES[:3])}
                      for _ in range(rng.randint(0, 5))],
            }
            conn = _sqlite(db)
            for name, pred, sql in _cases():
                with self.subTest(trial=trial, case=name):
                    expected = _sqlite_ids(conn, sql)
                    naive, planned = _ours(db, pred)
                    self.assertEqual(naive, expected, f"{name!r} SQL {sql!r}")
                    self.assertEqual(planned, expected, f"{name!r} SQL {sql!r}")

    def test_random_nested_predicates(self):
        """Fuzz AND/OR/NOT combinations of subquery predicates.

        Conjuncts that the planner can flatten sit at the top level; the
        wrappers exercise the row-by-row fallback.  Both must agree with
        SQLite for every generated NULL distribution.
        """
        rng = random.Random(20251120)
        atoms = _atoms()
        for trial in range(150):
            db = {
                "t": [{"id": i, "a": rng.choice(VALUES), "b": rng.choice(VALUES)}
                      for i in range(1, rng.randint(2, 6))],
                "s": [{"k1": rng.choice(VALUES), "k2": rng.choice(VALUES)}
                      for _ in range(rng.randint(0, 4))],
            }
            conn = _sqlite(db)
            for _ in range(8):
                pred, sql = _compose(rng, atoms, rng.randint(1, 3))
                expected = _sqlite_ids(conn, sql)
                naive, planned = _ours(db, pred)
                self.assertEqual(naive, expected,
                                 f"row-by-row mismatch for {sql!r}\npred={pred}")
                self.assertEqual(planned, expected,
                                 f"planned mismatch for {sql!r}\npred={pred}")


def _atoms():
    """(predicate, SQL) pairs used as leaves by the nested fuzzer."""
    return [
        ({"in": {"left": {"col": "a"}, "sub": {"from": "s", "select": ["k1"]}}},
         "a IN (SELECT k1 FROM s)"),
        ({"not_in": {"left": {"col": "b"}, "sub": {"from": "s", "select": ["k2"]}}},
         "b NOT IN (SELECT k2 FROM s)"),
        ({"in": {"left": [{"col": "a"}, {"col": "b"}],
                 "sub": {"from": "s", "select": ["k1", "k2"]}}},
         "(a, b) IN (SELECT k1, k2 FROM s)"),
        ({"not_in": {"left": [{"col": "a"}, {"col": "b"}],
                     "sub": {"from": "s", "select": ["k1", "k2"]}}},
         "(a, b) NOT IN (SELECT k1, k2 FROM s)"),
        ({"exists": {"sub": {"from": "s", "select": ["k1"], "corr": [["a", "k1"]]}}},
         "EXISTS (SELECT 1 FROM s WHERE s.k1 = t.a)"),
        ({"not_exists": {"sub": {"from": "s", "select": ["k1"], "corr": [["b", "k2"]]}}},
         "NOT EXISTS (SELECT 1 FROM s WHERE s.k2 = t.b)"),
        ({"exists": {"sub": {"from": "s", "select": ["k1"],
                             "corr": [["a", "k1"], ["b", "k2"]]}}},
         "EXISTS (SELECT 1 FROM s WHERE s.k1 = t.a AND s.k2 = t.b)"),
        ({"cmp": {"left": [{"col": "a"}, {"col": "b"}], "op": "<", "right": [2, None]}},
         "(a, b) < (2, NULL)"),
        ({"cmp": {"left": {"col": "a"}, "op": "=", "right": None}}, "a = NULL"),
        # IN (list) is = ANY(array) with full three-valued semantics, so it
        # stays correct when the fuzzer wraps it in NOT / AND / OR.
        ({"any": {"left": {"col": "a"}, "op": "=", "values": [1, None]}},
         "a IN (1, NULL)"),
    ]


def _compose(rng, atoms, depth):
    if depth <= 1 or rng.random() < 0.4:
        return rng.choice(atoms)
    kind = rng.choice(["and", "or", "not", "not"])
    if kind == "not":
        pred, sql = _compose(rng, atoms, depth - 1)
        return {"not": pred}, f"NOT ({sql})"
    left = _compose(rng, atoms, depth - 1)
    right = _compose(rng, atoms, depth - 1)
    joiner = "AND" if kind == "and" else "OR"
    return {"and" if kind == "and" else "or": [left[0], right[0]]}, \
        f"({left[1]}) {joiner} ({right[1]})"


if __name__ == "__main__":
    unittest.main()
