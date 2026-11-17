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
