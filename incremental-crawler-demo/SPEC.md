# Incremental Crawler Demo

Build a minimal incremental web crawler using Python standard library only.

## Requirements

1. `mock_server.py`: HTTP service exposing `GET /items?page=N` (N=1..3).
   Each page returns a JSON list of >=5 items with fields
   `id`, `name`, `price`, `updated_at`. Out-of-range pages return `[]`.
2. `crawler.py`: fetch all pages, parse, dedupe by `id`, persist to
   SQLite (`crawler.db`, table `items`). Persist a watermark
   (max `updated_at` seen, stored in `watermark.json`). On subsequent
   runs only insert records with `updated_at` greater than the watermark
   and print incremental stats (fetched / new / skipped).
3. `README.md`: how to run and verify.

## Acceptance

Start `mock_server.py`, run `crawler.py` twice; the second run's output
must show the incremental counts (new=0 if data unchanged).
