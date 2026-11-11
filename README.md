# Supplier NDJSON import

Resumable streaming NDJSON ingestion pipeline: supplier shards arrive
per day, may be appended in several rounds or redelivered, and one batch
may mix old and new field formats.

`fixtures/supplier-a.ndjson` is one logical append-only source. Offsets
refer to UTF-8 bytes in that source. A repeated file uses the same source
ID. An appended source retains its original bytes and ID.

## Layout

- `pipeline/stream.py` — byte-level line splitter: UTF-8 characters and
  records may span read chunks; an unterminated tail line waits for the
  next append and is finalized only on seal.
- `pipeline/normalizer.py` — versioned normalization rules (see below).
- `pipeline/store.py` — SQLite storage: records, rejects, conflicts and
  the checkpoint advance in one transaction.
- `pipeline/importer.py` — chunked ingest from the stored checkpoint.
- `pipeline/reference.py` — independent batch reference implementation
  used to verify record content and reject classification.
- `pipeline/corpus.py` — synthetic valid-corpus generator with coverage
  feedback (`artifacts/coverage.json`); business rejects come from the
  fixed samples in `fixtures/reject-samples.ndjson`.
- `tests/` — unit tests plus the differential regression (streaming
  pipeline vs. batch reference across chunk sizes, append orders and
  replay counts).
- `acceptance.py` / `run_acceptance.sh` — one-command local acceptance.

## Normalization rules

Version 1 stores `amount` as a decimal string; version 2 stores
`amount_cents` as an integer. Both normalize to an exact decimal with two
fractional places (`decimal.Decimal`) plus the currency. A missing `note`
and a null `note` are distinct (`note_state` = missing / null / present).

Explicit reject classification (`reason_code`): `invalid_utf8`,
`empty_line`, `invalid_json`, `not_an_object`, `duplicate_key`,
`missing_field`, `invalid_field`, `unknown_version`, `amount_precision`
(more than 2 fractional digits). Every stored row keeps the raw bytes,
SHA-256, source ID and byte offset.

Normalization versions: each row records the `norm_version` it was
written with (1: missing currency rejected; 2: missing currency defaults
to CNY). Replay never re-normalizes existing rows, so a new normalization
version cannot change the meaning of historical records.

## Replay and conflicts

Rows are keyed by `(source_id, byte_offset)`. Replaying identical bytes
at a source position is a no-op; different bytes at a position already
processed produce a row in `conflicts` and leave the original untouched.
`reset-checkpoint` restores an older checkpoint (e.g. from a backup) so
redelivered shards can be replayed.

## Usage

```sh
python3 -m pipeline.cli ingest DB SOURCE_ID PATH [--chunk-size N] [--seal]
python3 -m pipeline.cli dump DB SOURCE_ID
python3 -m pipeline.cli verify DB SOURCE_ID PATH
python3 -m pipeline.cli reset-checkpoint DB SOURCE_ID OFFSET
```

## Acceptance

```sh
./run_acceptance.sh
```

runs the unit + differential tests, then `acceptance.py`: baseline
ingest, process kill after-parse / in-transaction / after-commit with
replay of the original input (records, rejects and final checkpoint must
match the baseline), a conflict scenario, a normalization-version
scenario, and a 16 MB source ingested through an 8 KB buffer with peak
memory reported.

All rows are synthetic.
