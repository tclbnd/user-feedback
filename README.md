# etleq — symbolic equivalence checking for finite ETL rule versions

`etleq` decides whether two versions of a data-transformation rule produce the
same observable result — output field values **and** error classification — on
every supported input. It targets a finite, fully-specified fragment:
fixed-width integers, nullable/optional fields, conditional branches, and
statically bounded loops.

## What it checks

For every input in the (finite) input domain the two versions' observable
results are compared:

* each output field's presence, NULL-ness, and machine-integer value;
* the rule-level failure class (`E_MISSING`, `E_DIV_ZERO`, `E_DIV_TRAP`,
  `E_NULL_BRANCH`, `E_LOOP_BOUND`, or an explicit `E_EXPLICIT:*`).

Missing (absent key) and NULL (present `null`) are distinct. Verdict is one of:

* `equivalent` — proven equal over the complete domain;
* `different` — a deterministic minimal witness is produced, with both
  versions' field values, error class and branch position, replayed through an
  independent concrete interpreter;
* `unknown` — the rule leaves the supported fragment or a solver query exhausts
  its budget. Never a guess.

## Architecture

```
JSON rule
   │  parser.py      two surface forms → typed AST, width resolution, loc ids
   ▼
AST ───────────────► interpreter.py   independent concrete semantics (oracle)
   │
   ├─ symbolic.py    path-indexed symbolic execution: feasible-path pruning,
   │                 multi-level branch joins, static loop unrolling,
   │                 failure-partition splitting
   ▼
terms.py           immutable term/boolean language (two's-complement integers,
                   three-valued booleans, presence/null variables)
   │
   ├─ normalize.py   hash-consing, ITE/boolean-algebra folding, canonical order
   ▼
solver/native.py   dependency-free DPLL + interval contraction (ICP):
                   modular wrap arithmetic, inverse contractors, ITE case
                   splitting, bounded enumeration; budget → unknown
solver/z3_backend.py   optional adapter used only if z3 is installed
   │
   ▼
check.py           divide-and-conquer difference queries; small domains are
                   decided authoritatively by exhaustive concrete enumeration
```

The concrete interpreter shares no solving code with the symbolic engine, so
the acceptance harness can cross-validate one against the other.

## One-command acceptance

```
python3 accept.py
```

runs three phases:

1. finite-semantics spot checks (overflow wrap, truncating div/rem, the
   distinct failure classes, missing vs NULL);
2. small-domain exhaustive cross-validation — the symbolic verdict must match a
   full concrete enumeration, and the minimal witness must be the lex-first
   real difference and replay-confirmed;
3. wider-domain multi-branch / bounded-loop stress, reporting expanded states,
   pruned infeasible branches, loop unrolls and solver calls. Seeded
   differences at the overflow boundary, on a NULL branch and at a loop's last
   iteration are located exactly.

## Command line

```
python3 -m etleq.cli path/to/case.json            # text report
python3 -m etleq.cli path/to/case.json --json     # stable JSON verdict
```

Exit codes: `0` equivalent/different, `2` malformed or unsupported rule,
`3` unknown.

## Layout

* `etleq/` — parser, AST, terms, concrete interpreter, symbolic engine,
  normalizer, native solver, optional z3 adapter, checker, CLI;
* `fixtures/` — the original `price-rules.json` plus focused cases under
  `fixtures/cases/`;
* `docs/` — `rule-format.md` and `semantics.md`;
* `accept.py` — the one-command acceptance harness.

See `docs/rule-format.md` for the rule vocabulary and `docs/semantics.md` for
the exact finite semantics.
