# joinplan

The join planner of a small reporting engine. Given table statistics and a
query, it picks a join order with the lowest estimated cost, prints the plan
tree with per-node estimates, and can run the plan on in-memory rows.

    python3 joinplan.py plan <query.json>      # {"plan": tree, "rows": r, "cost": c}
    python3 joinplan.py explain <query.json>   # the plan as an indented tree
    python3 joinplan.py exec <query.json>      # {"columns": [...], "rows": [[...], ...]}
    python3 -m unittest discover -s tests

## Query

    {"tables": {"sales": {"rows": 1000000, "ndv": {"cid": 50000, "pid": 2000}},
                "customer": {"rows": 50000, "ndv": {"cid": 50000, "region": 8}}},
     "from": [{"table": "sales"},
              {"table": "customer", "on": [["sales.cid", "customer.cid"]]},
              {"table": "returns", "join": "left", "on": [["sales.id", "returns.sid"]]}],
     "where": [{"col": "customer.region", "op": "=", "value": 3},
               {"eq": ["sales.pid", "product.pid"]}],
     "shape": "bushy",
     "data": {"sales": [{"cid": 1, "pid": 2}, ...], ...}}

`from` is the written order; entries without `"join"` are inner joins (their
`on` may be empty). `ndv` lists every column with its number of distinct
values; rows in `data` have exactly those columns. Filter operators are `=`,
`!=`, `<`, `<=`, `>`, `>=`; a comparison with NULL is false. Inner `on`
conditions and `where` equalities never mention a left-joined table. `shape`
is `bushy` (default) or `left_deep` (the right input of every join is a
single table). `data` is only needed for `exec`.

## Plan tree

    {"op": "scan", "table": "t", "filters": [...], "rows": r, "cost": c}
    {"op": "join", "type": "inner" | "left" | "cross", "on": [[col, col], ...],
     "filters": [...], "left": node, "right": node, "rows": r, "cost": c}

## Estimates

- Inner `on` pairs and `where` equalities are equality predicates. Columns
  linked by them, directly or through other columns, form one equivalence
  class (`a.x = b.y` and `b.y = c.z` also link `a` and `c`).
- Filter selectivity: `=` is `1/ndv`, `!=` is `1 - 1/ndv`, ranges are `1/3`.
- Filters on a table are pushed into its scan, except filters on a
  left-joined table: they are applied on that left join node.
- Scan: `rows = table rows * product(filter selectivities)`,
  `cost = table rows`. The scan of a left-joined table carries no filters.
- Inner join of `L` and `R`: for every equivalence class with columns on both
  sides, take the smallest ndv among its columns on each side and multiply by
  `1 / max(ndv_left, ndv_right)`. `rows = rows(L) * rows(R) * product`. The
  node's `on` lists one column pair per such class. With no class spanning
  both sides the join is a `cross` product.
- Left join of `L` with table `T`: `inner = rows(L) * rows(T) *
  product(1 / max(ndv(a), ndv(b)) over its on pairs)`,
  `rows = max(rows(L), inner) * product(selectivities of T's filters)`.
- `cost(join) = cost(left) + cost(right) + rows(join)`.

## Search

Dynamic programming over table subsets considers every split of a subset
into two parts (bushy) or into a part plus one table (`left_deep`), and keeps
the cheapest plan per subset. A left-joined table `T` only enters as the right
input of its own left join, as a single table, once every other table named
in its `on` is on the left; it never takes part in an inner join by itself.
Cross products are considered only when the query's join graph is
disconnected; then any split is allowed. The result must be the same as
running the joins in the written order.
