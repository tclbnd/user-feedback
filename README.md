# winexec

The window function operator of a small query executor. It reads rows as JSON
lines and a window specification, and writes every input row back, in input
order, with one extra field per window function.

    python3 window.py <rows.jsonl> <spec.json> <out.jsonl> [--recompute] [--stats FILE]
    python3 -m unittest discover -s tests

## Specification

    {"partition_by": ["dept"],
     "order_by": [{"col": "day", "desc": false, "nulls": "last"}],
     "functions": [{"as": "moving", "fn": "avg", "arg": "amount",
                    "frame": {"mode": "rows", "start": ["preceding", 2], "end": ["current"]}}]}

`partition_by` and `order_by` may be empty. `desc` defaults to false; `nulls`
defaults to `last` for ascending and `first` for descending keys. Rows that
compare equal on every order key are peers; peers keep their input order.

Functions: `row_number`, `rank`, `dense_rank` (by peer groups);
`lag` / `lead` with `arg`, optional `offset` (default 1) and `default`
(default null), counted in rows inside the partition; and the frame functions
`first_value`, `sum`, `count`, `avg`, `min`, `max`. `count` without `arg` is
`COUNT(*)`; the others ignore NULL values. Over an empty frame (or only NULL
values) `count` is 0 and the others are null.

## Frames

`mode` is `rows`, `range` or `groups`; `start` and `end` are one of
`["unbounded_preceding"]`, `["preceding", n]`, `["current"]`,
`["following", n]`, `["unbounded_following"]`. Without a `frame` the default
is RANGE from unbounded preceding to current row when there is an ORDER BY,
and the whole partition otherwise.

- `rows`: offsets count rows; `current` is the current row.
- `groups`: offsets count peer groups; `current` is the current row's peer
  group (its first row for `start`, its last row for `end`).
- `range`: `current` is the current peer group as for `groups`. Offsets need
  exactly one order key and are distances in its value: numbers, or ISO dates
  (`YYYY-MM-DD`) with the offset in days. `preceding` means toward the start
  of the ordering (smaller values for ascending keys, larger for descending).
  When the current row's key is NULL, an offset bound stands for the NULL
  peer group itself; NULL rows are never within an offset of a non-NULL key.

## Incremental aggregates

Within a partition both ends of a frame only move forward, so `sum`, `count`
and `avg` keep a running state: each value enters the state once when the
frame end passes it and leaves once when the frame start passes it.
`--recompute` evaluates every frame from scratch instead; both modes must
produce identical output. `--stats` writes `{"agg_updates": N}`, the number of
values added to or removed from the running state of these three functions.
