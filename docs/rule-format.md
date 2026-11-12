# Rule format reference

An equivalence **case** is one JSON object with three keys:

| key | meaning |
| --- | --- |
| `input` | map of input field name → field specification |
| `old` | the incumbent rule version |
| `new` | the candidate rule version |

A field specification is `{"bits": 8|16|32|64, "signed": bool,
"nullable": bool, "optional": bool}`.

* `optional: false` means the field is always present.
* `nullable: false` means a present field is never SQL `NULL`.
* **Missing** (the key is absent from the record) and **NULL** (the key is
  present with value `null`) are distinct states and have distinct failure
  classes.

Both rule versions accept either of two surface forms.

## 1. Expression form

`{"output": {name: expr, ...}}` — every named output is computed directly from
an expression. The bundled `fixtures/price-rules.json` uses this form.

## 2. Statement form

`{"output": {name: spec}, "block": [stmt, ...]}` adds explicit control flow and
bounded loops. Output specs are `{"bits": n, "nullable": bool}`.

## Expressions

| JSON | meaning |
| --- | --- |
| `{"integer": v}` | machine integer constant |
| `{"field": "f"}` | read input field `f` |
| `{"var": "x"}` | read local `x`, falling back to an input field of the same name |
| `{"add":[a,b]}` `sub` `mul` `div` `rem` | fixed-width arithmetic |
| `{"wrap": {"bits": n, "arg": e}}` | reduce `e` to `n`-bit two's complement |
| `{"eq":[a,b]}` `ne` `lt` `le` `gt` `ge` | three-valued comparisons |
| `{"and":[...]}` `or` `not` | three-valued boolean logic |
| `{"is_missing": "f"}` | true iff field `f` is absent |
| `{"is_null": "f"}` (or `{"is_null": expr}`) | true iff `f`/`expr` is NULL |
| `{"if": [c, t, e]}` | three-valued conditional (NULL `c` raises `E_NULL_BRANCH`) |
| `{"error": "code"}` | fail with explicit classification `code` |

## Statements

| JSON | meaning |
| --- | --- |
| `{"let": {"name": "x", "value": e}}` | introduce a local |
| `{"set": {"name": "x", "value": e}}` | update an existing local |
| `{"if": {"cond": c, "then": [stmt...], "else": [stmt...]}}` | conditional block; a NULL condition is classified `E_NULL_BRANCH` |
| `{"loop": {"bound": e, "max": n, "index": "i", "init": {...}, "body": [stmt...]}}` | statically bounded loop, unrolled at most `n` times |
| `{"emit": {"output": name, "value": e}}` | assign an output field |
| `{"fail": "code"}` | terminate with explicit error `E_EXPLICIT:code` |

### Loop semantics

`bound` must evaluate to a non-negative integer no greater than `max`;
otherwise the rule fails with `E_LOOP_BOUND`. The `body` runs `bound` times with
the index local `i` taking `0 .. bound-1`. `init` declares the loop-local
accumulators. Each body iteration is a plain statement block, so conditionals
inside a loop participate in the same path-pruning machinery.

## Arithmetic widths

Operands of one arithmetic/comparison operator must share a width. An
unhinted integer literal adopts the other operand's width; mixing two real
field/variable widths requires an explicit `wrap`. This makes any truncation
intentional and visible in the rule text.
