# ETL rule symbolic equivalence checker

Verifies whether two versions of a data-warehouse field transformation rule
produce the **same output fields and the same error classification on every
supported input**.  The check is symbolic and exhaustive over the finite input
domain, not a sample-based diff.

## One-command acceptance

```
python3 -I scripts/acceptance.py
```

Runs four stages and exits non-zero on any failed assertion:

- **Stage C** — finite-semantics unit checks (fixed-width wrap/saturate/error
  overflow, truncating division, divide-by-zero, MISSING vs NULL distinction).
- **Stage A** — small-domain cases enumerated completely with an independent
  concrete interpreter; the symbolic verdict and its minimal witness must match
  the brute-force oracle, and every witness is replayed independently.
- **Stage B** — wide-domain (8/12-bit) multi-layer branch stress, reporting
  terminal states, pruned paths, merges and solver call counts; plus an
  overflow-boundary-only difference case.
- **Stage D** — confluence state merging, feasible-path pruning and honest
  UNDECIDABLE handling when the search budget is exhausted.

## Checking a rule file

```
python3 -I scripts/check_rule.py fixtures/price-rules.json          # text
python3 -I scripts/check_rule.py fixtures/price-rules.json --json   # stable JSON
```

## Finite semantics (`etl_eq/semantics.py`)

- **MISSING ≠ NULL.** MISSING means the key is absent from the record; NULL
  means the key is present with SQL NULL. Both propagate through arithmetic but
  are separately testable with `is_missing` / `is_null`.
- **Fixed-width integers** of 4/8/10/12/14/16/32/64 bits, signed or unsigned.
  Overflow policies: `wrap` (two's complement), `saturate` (clamp), `error`
  (raised as a classified rule error).
- **Integer division** truncates toward zero; division/remainder by zero and
  the signed `min / -1` edge are classified failures, not crashes.
- A rule either emits field values or terminates at an `error` node carrying a
  classification string and a branch location.

## Rule format (`etl_eq/rules.py`)

JSON documents with an `input` schema and `old`/`new` sections. Expressions are
tagged objects (`field`, `integer`, `add/sub/mul/div/rem`, `width`, `neg`,
`eq/ne/lt/le/gt/ge`, `is_missing`, `is_null`, `coalesce`, `fill_missing`,
`and/or/not`, `if`, `tag`, `tite`, `teq`, `error`); statements are `assign`,
`emit`, `if`, `error` and statically bounded `loop` (unrolled, max 64
iterations). Type override siblings `bits`/`signed`/`overflow` are accepted on
arithmetic nodes. `fixtures/price-rules.json` is a worked example.

## Components

| Module | Role |
| --- | --- |
| `semantics.py` | finite domain, widths, markers, outcomes, failure kinds |
| `rules.py` | rule format, parser, shared type inference |
| `concrete.py` | independent concrete interpreter — the semantic oracle |
| `fdsolver.py` | pure-Python finite-domain CSP solver (no third-party deps) |
| `symbolic.py` | symbolic execution, feasible-path pruning, confluence merge |
| `equivalence.py` | pairing, difference queries, minimal witnesses, verdicts |
| `exhaustive.py` | brute-force full-domain oracle used by acceptance |
| `cases.py` | synthetic rules incl. overflow/NULL/loop-last-iteration diffs |

## How equivalence is decided

Symbolic execution unrolls each rule into terminal path states that partition
the input domain exactly (every branch fork and failure guard is exhaustive).
Each old/new state pair gets feasibility then difference queries. Proving every
feasible pair observationally equal is a proof over the **complete** input
domain. On a difference, the lexicographically smallest satisfying input is the
minimal witness; it is replayed through the independent concrete interpreter
before being reported. Unsupported constructs or an exhausted solver budget
return `UNDECIDABLE` rather than a guess.
