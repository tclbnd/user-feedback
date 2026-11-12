# Finite semantics

The checker only reasons about a deliberately finite fragment. Every
observable result is either an output field value or a classified failure;
there is no undefined behaviour.

## Value lattice

For each field three mutually exclusive ranks exist, checked in this total
order (also used for deterministic witness minimisation):

1. **missing** — the field key is absent;
2. **null** — present with SQL `NULL`;
3. **value** — a machine integer in the field's representable range.

An input variable is modelled with one presence boolean, one null boolean, and
one integer term, with the axiom `null ⇒ present`.

## Fixed-width integers

Integers are `n`-bit two's-complement values. Every arithmetic result is
reduced modulo `2^n`; `wrap` is the explicit reduction operator. For example in
signed 8-bit:

* `127 + 1 = -128` (overflow wraps, it is not an error);
* `-128 - 1 = 127`;
* values stay inside `[-128, 127]`.

## Division and remainder

Division truncates **toward zero** (not floor), and the remainder takes the
sign of the dividend:

* `-7 / 3 = -2`, `-7 rem 3 = -1`.

Two classified failures exist:

* divisor `0` → `E_DIV_ZERO`;
* `INT_MIN / -1` (the unrepresentable quotient) → `E_DIV_TRAP`.

The symbolic engine builds these as explicit failure side-conditions attached
to the division term, so the failure class is part of the observable result,
not an afterthought.

## NULL three-valued logic

Comparisons and boolean operators follow SQL three-valued logic: any NULL
operand propagates NULL. A conditional whose condition evaluates to NULL does
not pick a branch; it raises the distinct class `E_NULL_BRANCH`. `is_missing`,
`is_null`, presence and the explicit failure classes always evaluate to a
definite TRUE/FALSE and are never themselves NULL.

## Failure classification

| class | raised when |
| --- | --- |
| `E_MISSING` | a field is read while absent |
| `E_NULL` | a non-nullable output receives NULL |
| `E_NULL_BRANCH` | an `if` condition is NULL |
| `E_DIV_ZERO` | division/remainder by zero |
| `E_DIV_TRAP` | `INT_MIN / -1` |
| `E_LOOP_BOUND` | a loop bound is negative or above its static `max` |
| `E_EXPLICIT:<name>` | a `fail`/`error` node in the rule |

The first failure in evaluation order wins (short-circuit), mirroring the
concrete interpreter. Error classes have a stable ordering so reports are
deterministic.

## Out-of-support and budget

Rules outside the supported vocabulary raise `UnsupportedRule` at parse time.
When the native solver cannot decide a feasibility query within its branch
budget, the verdict is **unknown** rather than a guessed equivalent/different.
`unknown` is a first-class, honest outcome; the CLI uses a distinct exit code.
