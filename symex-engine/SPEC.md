# Mini Symbolic Execution Engine

Implement a path-exploration engine that symbolically executes the
functions in `target.py` and finds concrete inputs reaching chosen
outcomes. Python standard library only — no z3 or other solvers;
write your own constraint solver.

## Deliverables

- `concolic.py`: execute a target function with symbolic inputs —
  for each branch, record the path condition over linear integer
  expressions, comparisons, `len()` of symbolic strings, `int()`,
  `.isdigit()`, `.startswith()`, `%`, `//`, `+`, `-`, `*`, `==`,
  `<`, `<=`, `>`, `>=`, `and`, `or`, `not`.
- `solver.py`: incremental linear-integer constraint solver —
  interval propagation + branching; string constraints handled via
  a finite automaton for `startswith`/char predicates.
- `explore.py`: BFS/DFS over the path tree with a depth bound and
  state budget; on reaching a target outcome (exception type or
  return value), emit the concrete input.
- `main.py`: `python3 main.py --func check_pin --goal AssertionError`
  prints the found input (e.g. `4213`) and the path condition.
- `README.md`

## Acceptance

1. `check_pin` + goal `AssertionError` → find input `4213`.
2. `classify` + goal `right` → find a valid right triangle
   (e.g. `3 4 5`).
3. `parse_level` + goal `ValueError` → find a string reaching
   `level overflow` (e.g. `"Lv100-S"`).
All three must be found automatically (no hard-coded answers),
each within a 120-second exploration budget. Print inputs + path
conditions.
