# aggq

The hash aggregation operator of a small query executor.

    python3 aggq.py <query.json>             # prints {"rows": [...], "spilled_partitions": N}
    python3 -m unittest discover -s tests

`query.json`:

    {"rows": [{"region": "eu", "product": null, "amt": 5}, ...],
     "grouping_sets": [["region", "product"], ["region"], []],     # or "group_by": [...] or "rollup": [...]
     "aggregates": [{"as": "n", "fn": "count", "arg": "*"},
                    {"as": "buyers", "fn": "count", "arg": "cust", "distinct": true},
                    {"as": "total", "fn": "sum", "arg": "amt"}],
     "having": [["n", ">=", 2], [{"fn": "sum", "arg": "amt"}, ">", 10]],
     "memory_groups": 1000}

- `group_by: [a, b]` is one grouping set; `rollup: [a, b]` means
  `[[a, b], [a], []]`. A missing column value is NULL (`null`).
- All NULLs of a grouping column form one group. Aggregates ignore NULL
  inputs, except `count` with `arg: "*"`. `fn` is `count`, `sum`, `min`,
  `max`, `avg` or `grouping`; `distinct: true` aggregates distinct non-NULL
  values. `sum`, `min`, `max` and `avg` of no non-NULL values are NULL;
  `count` is 0. `{"fn": "grouping", "arg": "col"}` returns 1 when `col` is
  not part of the row's grouping set (the value is a super-aggregate NULL)
  and 0 otherwise; it takes no `distinct` and ignores the row values.
- `having` conditions (all must hold) are checked after grouping. The left
  side is an output alias or an aggregate expression `{"fn", "arg",
  "distinct"}` that need not be selected. A comparison with NULL is false.
  `op` is one of `=`, `!=`, `<`, `<=`, `>`, `>=`.
- An empty grouping set `[]` produces one row even for empty input.

## Output

One object per group: the columns of all grouping sets (`null` for a column
not grouped in that set), the aggregate aliases, and `grouping`: a bitmask
over the columns in order of first appearance across the grouping sets, bit
`i` (value `1 << i`) set when column `i` is not part of the row's grouping
set. Rows are sorted by `grouping`, then by the column values with NULL first.

## Memory limit

At most `memory_groups` groups (default 1000) are held at once. Beyond that
the input is split into hash partitions by grouping key (doubling the number
of partitions until each fits) and each partition is aggregated separately;
`spilled_partitions` reports how many partitions were used (0 without
spilling). The result must be identical with or without spilling.
