# Coverage-Guided Greybox Fuzzer

Finds real crashes in `json_parser.c` using coverage-guided greybox
fuzzing. Python standard library + system clang only; the target's
defects are left untouched.

## Usage

```sh
python3 fuzzer.py --target json_parser.c --seeds seeds/ --time 300
python3 report.py
```

`fuzzer.py` writes deduped crashes to `crashes/` (`crash_NN_xxxxxx` plus a
minimised one-line variant `crash_NN_xxxxxx.min`, and `manifest.json`).
`report.py` rebuilds the target without coverage instrumentation and
re-runs every deduped crash, printing a reproduction table.

Options: `--out` (crash dir, default `crashes/`), `--build-dir`
(default `build/`), `--jobs` (parallel runners, default up to 8).

## How it works

1. **Instrumentation** — the target is compiled with
   `clang -fsanitize-coverage=trace-pc-guard` and linked against a small
   runtime (embedded in `fuzzer.py`, written to `build/cov_runtime.c`)
   that counts edge hits in a 64 KiB bitmap and dumps it to `$COV_OUT`
   when the process exits normally. AddressSanitizer is enabled so memory
   errors become observable, symbolised crashes.
2. **Coverage collection** — each run writes its input to a temp file,
   executes the binary, and reads back the edge bitmap; new edges are
   merged into a global map.
3. **Seed scheduling** — inputs that discover new edges join the corpus;
   the scheduler picks 50% from the 32 most recent interesting finds and
   50% uniformly from the whole corpus.
4. **Mutation** — 1–3 stacked ops chosen from: bit flips, random byte
   insert/delete/set, splicing two corpus inputs, dictionary token
   insertion (`{}[]":,`, `true`, `false`, `null`, `\u`), slice
   duplication (builds deep nesting / long strings) and slice deletion.
5. **Crash collection** — a crash is any exit other than the target's
   normal `0`/`2` (ASan exits 77, signals count too). Crashes are
   deduplicated by a top-of-stack signature
   (`asan:<error-type>:<top-frame-function>`, or `signal:<n>`). Each new
   signature is saved to `crashes/` and minimised with a greedy
   chunk-deletion (ddmin-style) loop that requires the identical
   signature, plus a newline-stripping pass for a one-line variant.
6. **Reporting** — `report.py` compiles the target without coverage
   instrumentation (ASan kept, which the spec allows) and checks that
   every deduped crash still crashes, comparing signatures with the
   fuzzing run.

## Bugs found in json_parser.c

The fuzzer reliably finds (among others):

- `asan:stack-buffer-overflow:parse_string` — `buf[256]` in
  `parse_string()` has no bounds check; a string literal longer than 255
  bytes overflows the stack buffer (BUG B).
- `asan:global-buffer-overflow:parse_array` — `depth` is an `int8_t`;
  more than 127 levels of nesting wrap it negative and `ctxstack[depth]`
  writes out of bounds (BUG C).

A third defect (blind `p += 4` after `\u`, BUG A) only reads past the
64 KiB static input buffer for near-maximal inputs and is not needed for
acceptance.
