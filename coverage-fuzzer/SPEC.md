# Coverage-Guided Greybox Fuzzer

Build a coverage-guided greybox fuzzer that finds real crashes in the
provided target `json_parser.c` (it contains defects — do not fix or
"improve" the target). Python standard library + system clang only.

## Deliverables

- `fuzzer.py`: full fuzzing loop —
  1. compile `json_parser.c` with edge coverage instrumentation
     (e.g. clang `-fsanitize-coverage` + runtime counters, or an
     equivalent bitmap mechanism you implement yourself);
  2. execute corpus inputs, collect an edge-coverage bitmap per run;
  3. seed scheduling: prefer inputs that discover new edges;
  4. mutation: bit flips, byte splice, dictionary tokens
     (`{}[]":,`, `true`, `false`, `null`, `\u`);
  5. crash collection: deduplicate by top-of-stack signature, keep
     the crashing input in `crashes/`, and produce a one-line
     minimised variant per crash.
- `report.py`: run the deduped crash inputs against the uninstrumented
  binary and print a table: crash id, dedup signature, minimised size,
  whether it still crashes.
- `README.md`

## Acceptance

`python3 fuzzer.py --target json_parser.c --seeds seeds/ --time 300`
must produce **at least 2 distinct crashes** (different dedup
signatures) within 5 minutes. Then `python3 report.py` must show the
deduped crashes still crash the plain binary. Print the summary.
