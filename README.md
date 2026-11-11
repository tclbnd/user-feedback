# Resumable streaming NDJSON collection & ingestion

A resumable, transactional pipeline for supplier NDJSON shards that arrive by
the day, may be appended or redelivered repeatedly, and mix schema versions in
the same batch. Pure Python standard library (SQLite, `decimal`, `json`,
`tracemalloc`, `trace`) — no external dependencies, tested on Python 3.9.

## Layout

```
ndjson_pipe/
  decoder.py            byte-oriented streaming NDJSON splitter (UTF-8 safe)
  normalize.py          versioned normalization, exact decimal money, reject codes
  store.py              SQLite: records / rejections / conflicts / checkpoints
  importer.py           resumable orchestration + 3 crash injection points
  reference.py          independent batch reference implementation
  compare.py            store-vs-reference and store-vs-store diffs
  crash_acceptance.py   subprocess kill-at-3-phases + replay comparison
  memory_acceptance.py  bounded-memory / tracemalloc peak measurement
  inspect_db.py         audit viewer (raw JSON samples, structured rejects)
  cli.py / __main__.py  command line entry point
tests/
  corpus.py             generated legal corpus + FIXED rejection samples
  test_decoder.py       UTF-8, cross-chunk lines, residual lines, CRLF
  test_normalize.py     every documented rule and rejection code
  test_pipeline.py      transactions, replay, conflict, resume, provenance
  test_differential.py  matrix vs the batch reference (chunks/appends/replays)
  test_crash_recovery.py hard os._exit at 3 phases + original-input replay
  test_memory.py        peak allocation must not scale with file size
  test_version_pinning.py simulated v3 cannot change historical v1/v2 rows
  test_coverage_edges.py  edge branches surfaced by coverage feedback
tools/
  coverage_feedback.py  stdlib trace coverage over the 4 core modules
  smoke_cli.py          CLI smoke: fixed rejects + idempotent replay
acceptance.sh           one-click local acceptance (4 stages)
RULES.md                the explicit processing rules (the specification)
fixtures/supplier-a.ndjson  shipped synthetic source
```

## Quick start

```bash
# import a source (chunk size and transaction batch size are explicit)
python3 -m ndjson_pipe.cli import --db out.db --source-id supplier-a \
    --file fixtures/supplier-a.ndjson --chunk-size 13 --batch-size 2

# redeliver the same file: full scan, identical content is idempotent,
# different content at the same offset becomes a conflict row
python3 -m ndjson_pipe.cli import --db out.db --source-id supplier-a \
    --file fixtures/supplier-a.ndjson --full-scan

# inspect retained raw JSON samples, normalized rows and structured rejects
python3 -m ndjson_pipe.inspect_db --db out.db --source-id supplier-a

# unsealed source: a trailing partial line is held for the next append
python3 -m ndjson_pipe.cli import --db out.db --source-id s --file part.ndjson --sealed false
```

## One-click acceptance

```bash
./acceptance.sh
```

Stages: (1) unit + differential regression, (2) coverage feedback over the
streaming/normalization modules, (3) hard process kills at three phases with
original-input replay and full record/rejection/conflict/checkpoint comparison,
(4) bounded-memory run on a source much larger than the read buffer with peak
memory report. Exit code is non-zero on any failure.

## Where the requirements live

- UTF-8 / cross-chunk records / residual tail / byte offsets → `decoder.py`
- duplicate keys, precision overflow, null vs missing, exact decimal money →
  `normalize.py` and the rule table in `RULES.md`
- record + rejection + checkpoint in one transaction, replay idempotency,
  same-offset-different-content conflicts, monotonic checkpoint → `store.py`, `importer.py`
- version pinning so new versions never change historical rows →
  `_NORMALIZERS` registry + `tests/test_version_pinning.py`
- independent batch reference + differential matrix (chunk sizes, append
  order, replay counts) → `reference.py`, `tests/test_differential.py`
- coverage feedback expands the *legal* corpus first; business rejects are
  fixed samples → `tests/corpus.py`, `tools/coverage_feedback.py`
- kill before-persist / in-transaction / after-commit then replay →
  `crash_acceptance.py`, `tests/test_crash_recovery.py`
- incremental memory on data larger than the read buffer, peak report →
  `memory_acceptance.py`, `tests/test_memory.py`
