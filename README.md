# sortexec

The sort operator of a small query executor. It reads rows as JSON lines,
sorts them by an ORDER BY clause and writes them out, under a memory quota
(`work_mem`, counted in rows). Inputs larger than the quota are sorted
externally: sorted runs are spilled to temporary files and merged.

    python3 sort.py <in.jsonl> <out.jsonl> --order "<order by>" [--limit N]
                    [--work-mem ROWS] [--fan-in K] [--tmpdir DIR] [--stats FILE]
    python3 -m unittest discover -s tests

Defaults: `--work-mem 10000`, `--fan-in 8` (2 ≤ K ≤ work_mem), `--tmpdir` the
system temporary directory. Input is one JSON object per line (blank lines
skipped); output is the same objects in sorted order, only the first N with
`--limit`.

Exit status: 0 on success, 2 for bad input or arguments (a malformed JSON
line, an unknown collation), 137 when the operator is killed for holding more
than `work_mem` rows. Whatever the exit status, no file may be left behind in
the temporary directory.

## ORDER BY

Comma-separated keys: `column [COLLATE binary|nocase] [ASC|DESC] [NULLS FIRST|NULLS LAST]`,
keywords case-insensitive. Defaults follow PostgreSQL: `ASC`; `NULLS LAST` for
ascending keys and `NULLS FIRST` for descending keys. A missing column or JSON
`null` is NULL. Numbers compare numerically and sort before strings.
`COLLATE binary` (default) compares strings by code point; `COLLATE nocase`
compares `str.casefold()` of both, and strings equal after folding are equal
keys. Rows with equal keys keep their input order (the sort is stable), also
for descending keys and with `--limit`.

## Memory quota

The operator never holds more than `work_mem` rows. If the input has at most
`work_mem` rows it is sorted in memory and nothing is spilled. Otherwise it is
cut into runs of exactly `work_mem` consecutive rows (the last may be shorter),
each sorted and written to a temporary file. While more than `fan-in` runs
remain, consecutive groups of `fan-in` runs are merged into one new run each;
that is one merge pass. The final pass merges the remaining runs into the
output and also counts as a pass.

With `--limit N` and N ≤ work_mem the operator keeps only the best N rows in a
bounded heap and never spills; with N > work_mem it uses the external sort.

`--stats` writes `{"rows": N, "runs": R, "merge_passes": P}`: input rows,
initial runs written to disk, and merge passes (both 0 when nothing spilled).

## Implementation notes

* Rows are represented by an ascending key tuple. DESC is expressed by a
  comparison wrapper that flips `<` but keeps `==` as value equality, so a
  single ascending sort handles mixed directions and NULLS FIRST/LAST, and
  equal keys keep input order.
* `nocase` compares `casefold()`ed strings only, with no raw-string
  tie-breaker, so strings that fold to the same text are genuinely equal keys.
* The bounded top-N heap is a max-heap over the key; among equal keys the
  *latest* retained row is at the root and is evicted first, so the earliest
  equal-key rows survive and the LIMIT output is stable.
* The k-way merge streams one row per run and breaks ties by run index (then
  by read order within a run), never comparing row payloads. Peak residency
  is `work_mem` rows for the buffer and `fan_in` rows for the merge frontier.
* Temporary files are created with `mkstemp` in `--tmpdir` and removed in a
  `finally`: on success, on bad input, on an internal crash, when the consumer
  stops early, and on `SIGINT`/`SIGTERM`/`SIGHUP` (handled by unwinding the
  stack so the `finally` blocks run).
* NaN (only reachable through a JSON number literal such as `NaN`) sorts
  after every other number, as in PostgreSQL.
