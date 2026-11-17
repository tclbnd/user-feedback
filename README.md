# joinexec

The hash join operator of a small row-oriented query executor. Both inputs
are JSON-lines files of objects; the right input is the build side and the
left input is the probe side. The operator runs under a fixed memory quota
(`work_mem`) counted in build rows.

## Commands

    python3 join.py <left.jsonl> <right.jsonl> <out.jsonl> --type TYPE --on LCOL=RCOL
                    [--work-mem ROWS] [--fanout F] [--max-depth D]
                    [--tmpdir DIR] [--stats FILE]
    python3 -m unittest discover -s tests

| option        | default        | meaning                                            |
|---------------|----------------|----------------------------------------------------|
| `--type`      | required       | `inner`, `left`, `right`, `semi` or `anti`          |
| `--on`        | required       | equality condition `left_column=right_column`       |
| `--work-mem`  | 10000          | most build rows held in memory at once              |
| `--fanout`    | 8              | partitions created by one partitioning step         |
| `--max-depth` | 4              | deepest partition level allowed                     |
| `--tmpdir`    | system default | where partition files are created                   |
| `--stats`     | none           | write a JSON summary of the join to this file       |

Exit status: 0 on success, 2 for bad input or arguments, 3 when a partition
still exceeds `work_mem` at `--max-depth`, 137 when the operator is killed for
holding more than `work_mem` build rows in its hash table. Temporary files are
removed whatever the exit status.

## Output

One JSON object per line; the order of output lines is not significant.

- `inner`, `left`, `right`: `{"l": <left row or null>, "r": <right row or null>}`
  for every matching pair, plus for `left` every left row without a match
  (`"r": null`) and for `right` every right row without a match (`"l": null`).
- `semi`: every left row that has at least one match, once.
- `anti`: every left row that has no match, once.

## Key semantics

A key is the value of the join column; a missing column or a JSON `null` is
NULL. Keys are integers or strings, and an integer never equals a string
(`1` does not match `"1"`). NULL is not equal to anything, NULL included: a
row whose key is NULL never matches. Such a left row still appears in `left`
and `anti` output, and such a right row still appears in `right` output.

## Memory quota and partitioning

Only build rows with a non-NULL key are put in the hash table. If there are at
most `work_mem` of them, the join runs in memory and nothing is written to
disk.

Otherwise both inputs are split into `fanout` partitions written to temporary
files; a row with key `k` goes to partition

    int(hashlib.sha1(f"{level}:{json.dumps(k)}".encode()).hexdigest(), 16) % fanout

where `level` is 1 for this first split. Rows whose key is NULL are not put in
any partition. Each partition is then joined on its own: if its build side has
at most `work_mem` rows it is joined in memory, otherwise it is split again in
the same way with `level + 1`. A partition that would need a split beyond
`--max-depth` aborts the join with exit status 3.

## Stats file

    {"build_rows": 6000, "probe_rows": 5000, "spilled_partitions": 11, "max_depth": 2}

`build_rows` and `probe_rows` count all input rows. `spilled_partitions` counts
the partitions, at every level, that received at least one build row.
`max_depth` is the deepest partition level created (0 for an in-memory join).
