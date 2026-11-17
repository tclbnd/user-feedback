# widesplit

Schema design and migration tool for a legacy wide table. From the declared
functional dependencies it computes attribute closures, a minimal cover and
all candidate keys, synthesizes a dependency-preserving 3NF schema, checks
whether BCNF is reachable and which dependencies a BCNF split would lose, and
emits DDL plus data migration SQL after checking the sample data against the
dependencies.

    python3 normalize.py closure <spec.json> A,B          # JSON list, schema order
    python3 normalize.py analyze <spec.json>              # JSON report
    python3 normalize.py migrate <spec.json> <sample.csv> <outdir>
    python3 -m unittest discover -s tests

## Spec

```json
{"table": "orders_wide",
 "attributes": ["order_id", "line_no", "customer_id", "city", "zip"],
 "types": {"order_id": "INTEGER"},
 "fds": [{"lhs": ["order_id"], "rhs": ["customer_id"]},
         {"lhs": ["zip"], "rhs": ["city"]}]}
```

`attributes` fixes the column order used everywhere in the output; `types`
defaults to `TEXT`. The sample CSV has a header row with the attribute names
and no empty values.

## Algorithms

* **Closure**: repeat until nothing changes.
* **Minimal cover**: split right sides into single attributes (input order,
  dropping trivial and duplicate ones); for each dependency in turn, drop
  left attributes (in schema order) that are extraneous; then drop
  dependencies implied by the remaining ones, in order. The result has
  single-attribute right sides, no extraneous left attribute and no
  redundant dependency, and is equivalent to the input.
* **Candidate keys**: every minimal attribute set whose closure is the whole
  table, all of them, whatever their size.
* **3NF synthesis**: one relation per left side of the minimal cover (left
  side plus all its right sides, key = left side); drop a relation contained
  in another; if no relation contains a candidate key, add one made of the
  first candidate key. Relations are named `<table>_1`, `<table>_2`, … in
  that order.
* **BCNF**: a relation violates BCNF when some subset `X` of it determines
  other attributes of the relation without determining all of them
  (dependencies projected from the full closure). `is_bcnf` tells whether
  every 3NF relation is in BCNF. Otherwise each violating relation is split
  on its first violating `X` (smaller sets first, then schema order) into
  `X+ ∩ R` and `X ∪ (R − X+)`, repeatedly. A minimal cover dependency is
  **lost** when the union of the dependencies projected onto the BCNF
  relations no longer implies it (the standard preservation test, not just
  "some relation contains all its attributes").

## Report (`analyze`)

```json
{"minimal_cover": [{"lhs": [...], "rhs": [a]}],
 "candidate_keys": [[...]],
 "relations": [{"name", "attributes", "key"}],
 "is_bcnf": false,
 "bcnf_relations": [[...]],
 "lost_dependencies": [{"lhs": [...], "rhs": [a]}]}
```

## Migration (`migrate`)

First every declared dependency (as written in the spec, all its right-hand
attributes) is checked on the sample: if two rows agree on the left side but
differ on any right-hand attribute, the command prints
`{"violations": [{"lhs", "rhs", "values": [left side values]}]}` (one entry
per violated dependency, first offending group) and exits with status 3
without writing anything. Otherwise it writes `schema.sql` (one `CREATE TABLE`
per 3NF relation, with `PRIMARY KEY (key)`) and `migrate.sql` (one
`INSERT INTO <relation> (...) SELECT DISTINCT ... FROM <table>;` per
relation) into `<outdir>` and prints both. Joining the new tables back with
natural joins gives the original rows.
