"""Pure-Python finite-domain constraint solver.

No third-party SMT dependency is available in this environment, so the
symbolic engine targets a self-contained CSP solver over finite domains.
Every supported input domain is finite anyway (fixed-width integers plus the
MISSING/NULL markers), which makes the reduction exact rather than
approximate: a feasibility query here is a genuine proof over the whole
input domain, bounded only by an explicit search budget.

Two techniques keep wide domains (e.g. u16/i16) tractable:

* interval projection revision for linear arithmetic and comparisons, so
  propagation never enumerates 65k values;
* fail-first DFS that only branches on marker/bool variables and small
  residual integer domains, then verifies remaining arithmetic constraints
  directly.  When a query is genuinely beyond the budget,
  :class:`~etl_eq.semantics.BudgetExceeded` is raised and the checker reports
  UNDECIDABLE instead of guessing.

Trail nodes (all immutable tuples, hashable)
--------------------------------------------
``("v", name, type)``                solver variable
``("c", value)``                     constant (int/bool/MISSING/NULL/None)
``("arith", op, IntType, a, b)``     fixed-width arithmetic (wrap/saturate)
``("ovf", op, IntType, a, b)``       bool: same arithmetic overflows
``("divz", a, b)``                   bool: b == 0 (div/rem only)
``("width", IntType, a)`` / ``("neg", IntType, a)``
``("cmp", op, a, b)``                bool, UNKNOWN (None) on markers
``("miss", name)``/``("null", name)``
``("logic", op, a, b)``/``("not", a)``
``("ite", c, t, e)`` / ``("tite", c, t, e)``
``("tag", a)`` / ``("teq", a, b)``
``("err", code)``                    error-valued term
``("loc", location, a)``             node annotated with a branch location
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .semantics import (
    BudgetExceeded,
    IntType,
    MISSING,
    NULL,
    value_order_key,
)

UNKNOWN = None


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    feasibility_calls: int = 0
    propagations: int = 0
    dfs_branches: int = 0
    budget_hits: int = 0


class Domain:
    """A finite domain of ints and marker/bool/error symbols."""

    __slots__ = ("values",)

    def __init__(self, values):
        self.values = frozenset(values)

    @staticmethod
    def int_range(t: IntType) -> "Domain":
        return Domain(range(t.min, t.max + 1))

    def is_empty(self) -> bool:
        return not self.values

    def is_singular(self) -> bool:
        return len(self.values) == 1

    def min(self) -> Any:
        return min(self.values, key=value_order_key)

    def int_bounds(self) -> Optional[Tuple[int, int]]:
        ints = [v for v in self.values if isinstance(v, int) and not isinstance(v, bool)]
        if not ints:
            return None
        return min(ints), max(ints)

    def __contains__(self, v) -> bool:
        return v in self.values

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self):
        return iter(self.values)

    def __repr__(self) -> str:
        if len(self.values) <= 6:
            return "{" + ",".join(map(str, sorted(self.values, key=value_order_key))) + "}"
        return f"<domain size={len(self.values)}>"


# ---------------------------------------------------------------------------
# Expression evaluation
# ---------------------------------------------------------------------------

def node_vars(node: tuple, out: set) -> None:
    if node[0] == "v":
        out.add(node[1])
        return
    if node[0] in ("c", "err"):
        return
    if node[0] in ("miss", "null"):
        out.add(node[1])
        return
    if node[0] == "loc":
        node_vars(node[2], out)
        return
    for child in node[1:]:
        if isinstance(child, tuple):
            node_vars(child, out)


def _has_marker_conflict(terms) -> bool:
    """True if an AND term list requires one variable to be two markers."""
    seen: Dict[str, set] = {}
    for t in terms:
        if t[0] in ("miss", "null"):
            kind = seen.setdefault(t[1], set())
            kind.add(t[0])
            if len(kind) > 1:
                return True
    return False


def normalize(node):
    """Constant folding and boolean identity simplification.

    Applied once when a constraint is added so propagation never repeatedly
    interprets wrappers such as ``(and, c, TRUE)`` or nested negations.  The
    rewrite is value-preserving for every assignment.
    """
    if not isinstance(node, tuple):
        return node
    tag = node[0]
    if tag in ("v", "c", "err", "miss", "null"):
        return node
    children = [normalize(c) if isinstance(c, tuple) else c for c in node[1:]]
    node = (tag, *children)

    if tag in ("logic",):
        _, op, a, b = node

        def flatten(n, wanted_op):
            if n[0] == "logic" and n[1] == wanted_op:
                return flatten(n[2], wanted_op) + flatten(n[3], wanted_op)
            return [n]

        terms = flatten(a, op) + flatten(b, op)
        # constant folding over the flattened term list
        if op == "and":
            if any(t == ("c", False) for t in terms):
                return ("c", False)
            terms = [t for t in terms if t != ("c", True)]
            if not terms:
                return ("c", True)
            # contradiction: x and (nottrue x)
            negated = {t[1] for t in terms if t[0] == "nottrue"}
            if any(t in negated for t in terms):
                return ("c", False)
            # miss/null on same variable are mutually exclusive
            if _has_marker_conflict(terms):
                return ("c", False)
        else:
            if any(t == ("c", True) for t in terms):
                return ("c", True)
            terms = [t for t in terms if t != ("c", False)]
            if not terms:
                return ("c", False)
            negated = {t[1] for t in terms if t[0] == "nottrue"}
            if any(t in negated for t in terms):
                return ("c", True)
        # dedupe structurally identical terms
        unique = []
        for t in terms:
            if t not in unique:
                unique.append(t)
        terms = unique
        result = terms[0]
        for t in terms[1:]:
            result = ("logic", op, result, t)
        return result
    if tag == "not":
        inner = node[1]
        if inner == ("c", True):
            return ("c", False)
        if inner == ("c", False):
            return ("c", True)
        if inner == ("c", None):
            return ("c", None)
        if inner[0] == "not":
            return inner[1]
        return node
    if tag in ("istrue", "nottrue"):
        inner = node[1]
        if inner[0] == "c":
            val = inner[1]
            res = (val is True) if tag == "istrue" else (val is not True)
            return ("c", res)
        return node
    if tag in ("nottrue",):
        return node
    return node


def evaluate(node: tuple, assign: Dict[str, Any]) -> Any:
    tag = node[0]
    if tag == "v":
        return assign.get(node[1], MISSING)
    if tag == "c":
        return node[1]
    if tag == "err":
        return ("ERROR", node[1])
    if tag == "loc":
        return evaluate(node[2], assign)
    if tag == "miss":
        return assign.get(node[1], MISSING) is MISSING
    if tag == "null":
        v = assign.get(node[1], MISSING)
        return v is NULL or v is None
    if tag == "width":
        v = evaluate(node[2], assign)
        return v if v is MISSING or v is NULL else node[1].wrap_value(v)
    if tag == "neg":
        v = evaluate(node[2], assign)
        return v if v is MISSING or v is NULL else node[1].wrap_value(-v)
    if tag == "arith":
        return _eval_arith(node, assign)[0]
    if tag == "ovf":
        return _eval_arith(node, assign)[1]
    if tag == "divz":
        return evaluate(node[2], assign) == 0
    if tag == "cmp":
        return _eval_cmp(node[1], evaluate(node[2], assign), evaluate(node[3], assign))
    if tag == "logic":
        return _eval_logic(node[1], evaluate(node[2], assign), evaluate(node[3], assign))
    if tag == "not":
        v = evaluate(node[1], assign)
        return None if v is None else not v
    if tag in ("ite", "tite"):
        c = evaluate(node[1], assign)
        return evaluate(node[2], assign) if c is True else evaluate(node[3], assign)
    if tag == "tag":
        v = evaluate(node[1], assign)
        return isinstance(v, int) and not isinstance(v, bool)
    if tag == "teq":
        a, b = evaluate(node[1], assign), evaluate(node[2], assign)
        if a is MISSING or a is NULL or b is MISSING or b is NULL:
            return a is b
        return a == b
    if tag == "istrue":
        return evaluate(node[1], assign) is True
    if tag == "nottrue":
        return evaluate(node[1], assign) is not True
    raise AssertionError(f"cannot evaluate {tag}")  # pragma: no cover


def _eval_arith(node, assign):
    _, op, rtype, an, bn = node
    a, b = evaluate(an, assign), evaluate(bn, assign)
    if a is MISSING or b is MISSING:
        return MISSING, MISSING
    if a is NULL or b is NULL:
        return NULL, NULL
    raw = _raw_binop(op, a, b)
    if raw is None:
        return "DIV0", True
    overflow = not rtype.fits(raw)
    if rtype.overflow == "wrap":
        val = rtype.wrap_value(raw)
    elif rtype.overflow == "saturate":
        val = min(rtype.max, max(rtype.min, raw)) if overflow else raw
    else:
        val = ("OVF", rtype) if overflow else raw
    return val, overflow


def _raw_binop(op: str, a: int, b: int) -> Optional[int]:
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        return a * b
    if op in ("div", "rem"):
        if b == 0:
            return None
        if op == "div":
            return int(a / b) if a * b < 0 else a // b
        q = int(a / b) if a * b < 0 else a // b
        return a - q * b
    raise AssertionError(op)  # pragma: no cover


def _eval_cmp(op: str, a: Any, b: Any) -> Any:
    if a is MISSING or a is NULL or b is MISSING or b is NULL:
        return UNKNOWN
    return bool({
        "eq": a == b, "ne": a != b, "lt": a < b,
        "le": a <= b, "gt": a > b, "ge": a >= b,
    }[op])


def _eval_logic(op: str, a: Any, b: Any) -> Any:
    if op == "and":
        if a is False or b is False:
            return False
        if a is None or b is None:
            return None
        return True
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def values_equal(a: Any, b: Any) -> bool:
    if a is MISSING or a is NULL or b is MISSING or b is NULL:
        return a is b
    return a == b


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

@dataclass
class Constraint:
    kind: str
    nodes: tuple
    vars: frozenset


# A domain product is "small" when exhaustive support checks are cheap.
SMALL_LIMIT = 256


class FiniteDomainSolver:
    """AC-3 propagation with interval shortcuts plus fail-first DFS."""

    def __init__(self, budget: int = 200_000):
        self.domains: Dict[str, Domain] = {}
        self.kinds: Dict[str, str] = {}
        self.constraints: List[Constraint] = []
        self.budget = budget
        self.stats = Stats()
        self._steps = 0
        self._revise_cache: Dict[tuple, tuple] = {}

    def _reset_search(self):
        self._steps = 0
        self._revise_cache = {}

    # -- construction -----------------------------------------------------
    def add_var(self, name: str, domain: Domain, kind: str = "int") -> None:
        self.domains[name] = domain
        self.kinds[name] = kind

    def add_constraint(self, kind: str, *nodes: tuple) -> None:
        norm_nodes = tuple(normalize(n) for n in nodes)
        vs: set = set()
        for n in norm_nodes:
            node_vars(n, vs)
        self.constraints.append(Constraint(kind, norm_nodes, frozenset(vs)))

    # -- queries ----------------------------------------------------------
    def feasible(self) -> bool:
        self.stats.feasibility_calls += 1
        self._reset_search()
        if self._constant_conflict():
            return False
        try:
            return self._search({k: Domain(v.values) for k, v in self.domains.items()}) is not None
        except BudgetExceeded:
            self.stats.budget_hits += 1
            raise

    def find_minimal_model(self, var_order: Sequence[str]):
        """Lexicographically smallest satisfying assignment (MISSING < NULL <
        ints, False < UNKNOWN < True) for deterministic minimal witnesses."""
        self.stats.feasibility_calls += 1
        self._reset_search()
        if self._constant_conflict():
            return None
        try:
            return self._minimal(
                {k: Domain(v.values) for k, v in self.domains.items()},
                list(var_order), 0,
            )
        except BudgetExceeded:
            self.stats.budget_hits += 1
            raise

    # -- search -----------------------------------------------------------
    def _constant_conflict(self) -> bool:
        """True if any fully-constant constraint is already violated."""
        for con in self.constraints:
            if not con.vars and all(isinstance(n, tuple) and n[0] == "c"
                                    for n in con.nodes):
                if not self._check(con, {}):
                    return True
        return False

    def _tick(self) -> None:
        self._steps += 1
        if self._steps > self.budget:
            raise BudgetExceeded(f"solver budget {self.budget} exhausted")

    def _search(self, domains):
        domains, ok = self._propagate(domains)
        if not ok:
            return None
        if all(len(d) == 1 for d in domains.values()):
            model = {k: next(iter(d.values)) for k, d in domains.items()}
            # Full verification: arcs too large to revise are decided here,
            # exactly, over a complete assignment.
            if all(self._check(c, model) for c in self.constraints):
                return model
            return None
        name = self._pick_var(domains)
        if name is None:
            # Every solver-owned variable is singular; no further branching
            # is possible.  A missing key at leaf check means the partial
            # model cannot satisfy the constraints, so this branch is dead.
            return None
        self.stats.dfs_branches += 1
        for val in sorted(domains[name].values, key=value_order_key):
            self._tick()
            found = self._search({**domains, name: Domain((val,))})
            if found is not None:
                return found
        return None

    def _minimal(self, domains, var_order, idx):
        domains, ok = self._propagate(domains)
        if not ok:
            return None
        if all(len(d) == 1 for d in domains.values()):
            model = {k: next(iter(d.values)) for k, d in domains.items()}
            if all(self._check(c, model) for c in self.constraints):
                return model
            return None
        name = self._next_ordered_var(domains, var_order, idx)
        if name is None:
            return None
        for val in sorted(domains[name].values, key=value_order_key):
            self._tick()
            # idx is left unchanged: the chosen var becomes singular and the
            # next call naturally advances to the following ordered var
            found = self._minimal({**domains, name: Domain((val,))}, var_order, idx)
            if found is not None:
                return found
        return None

    def _next_ordered_var(self, domains, var_order, idx):
        for name in var_order[idx:]:
            if name in domains and len(domains[name]) > 1 and name in self.domains:
                return name
        return self._pick_var(domains)

    def _pick_var(self, domains) -> Optional[str]:
        # only variables actually owned by the solver may be branched on;
        # constant sub-expressions introduce no own variables
        candidates = [(n, d) for n, d in domains.items()
                      if len(d) > 1 and n in self.domains]
        if not candidates:
            return None
        # markers/bools first (tiny domains), then smallest integer domain
        return min(
            candidates,
            key=lambda nd: (
                0 if self.kinds[nd[0]] != "int" else 1,
                len(nd[1]),
                nd[0],
            ),
        )[0]

    # -- propagation ------------------------------------------------------
    def _propagate(self, domains):
        queue = list(range(len(self.constraints)))
        queued = set(queue)
        while queue:
            idx = queue.pop(0)
            queued.discard(idx)
            con = self.constraints[idx]
            self._tick()
            self.stats.propagations += 1
            changed = False
            for name in con.vars:
                kept = self._revise(con, name, domains)
                if kept is None:
                    return domains, False
                if len(kept) != len(domains[name].values):
                    domains[name] = Domain(kept)
                    changed = True
            if changed:
                for j, other in enumerate(self.constraints):
                    if j != idx and j not in queued and other.vars & con.vars:
                        queue.append(j)
                        queued.add(j)
        return domains, True

    def _revise(self, con: Constraint, target: str, domains):
        """Constraint-directed revision.

        Each arc is filtered with an interval/set projection linear in the
        target domain size; generic enumeration is only used for tiny
        residual products.  Results are memoized on the fingerprints of the
        constraint's variable domains, so an arc that is re-queued without
        any relevant domain change costs one dictionary lookup.
        """
        fingerprint = tuple(sorted(
            (v, domains[v].values) for v in con.vars))
        cache_key = (id(con), target, fingerprint)
        cached = self._revise_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        result = self._revise_uncached(con, target, domains)
        if result is not None:
            self._revise_cache[cache_key] = tuple(result)
        return result

    def _revise_uncached(self, con: Constraint, target: str, domains):
        projected = self._project_revise(con, target, domains)
        if projected is not None:
            return projected
        current = list(domains[target].values)
        product = 1
        for v in con.vars:
            if v != target:
                product *= len(domains[v])
        if len(current) * product <= SMALL_LIMIT:
            kept = []
            for val in current:
                if self._enumerative_support(con, target, val, domains):
                    kept.append(val)
            return kept
        # Too large to revise this arc: keep the domain (sound), let DFS and
        # full-assignment checks do the work.
        return current

    def _project_revise(self, con, target, domains):
        kind = con.kind
        node = con.nodes[0] if kind in ("true", "false") else None
        if node is not None and node[0] == "loc":
            node = node[2]
        want = kind == "true"

        # Unary constraints (the common case after branching: miss/null/tag,
        # logic trees over one input, etc.) are decided per value exactly.
        if con.vars == frozenset((target,)):
            return self._project_unary(con, target, domains)

        if node is not None and node[0] in ("miss", "null"):
            if target != node[1]:
                return list(domains[target].values)
            marker = MISSING if node[0] == "miss" else NULL
            cur = list(domains[target].values)
            if want:
                return [v for v in cur if v is marker]
            return [v for v in cur if v is not marker]

        if node is not None and node[0] == "divz":
            if target not in con.vars:
                return list(domains[target].values)
            cur = list(domains[target].values)
            return [v for v in cur if (v == 0) == want]

        if node is not None and node[0] in ("logic", "not", "tag", "teq", "istrue", "nottrue"):
            return self._project_bool(node, target, domains, want, con)

        if node is not None and node[0] == "cmp":
            return self._project_cmp(node, target, domains, want, con)

        if node is not None and node[0] == "ovf":
            # boundary sets are discontinuous: enumerate only when cheap
            return None

        if kind in ("eqval", "neval"):
            if node is not None and node[0] == "arith":
                return self._project_arith(node, target, domains, con)
            return self._project_eqval(con, target, domains)
        return None

    def _project_unary(self, con, target, domains):
        """Exact projection for a constraint over exactly one variable."""
        cur = list(domains[target].values)
        kept = []
        for val in cur:
            if self._check(con, {target: val}):
                kept.append(val)
        return kept

    # -- projection helpers ----------------------------------------------
    def _project_bool(self, node, target, domains, want, con):
        cur = list(domains[target].values)
        if target not in con.vars:
            return cur
        others = [v for v in con.vars if v != target]
        product = 1
        for v in others:
            product *= len(domains[v])
        if len(cur) * product > SMALL_LIMIT:
            return cur  # exact at full-assignment time
        lists = [list(domains[v].values) for v in others]
        kept = []
        for val in cur:
            if self._has_support_values(con, target, val, others, lists):
                kept.append(val)
        return kept

    def _has_support_values(self, con, target, val, others, lists):
        def rec(i, assign):
            if i == len(others):
                assign[target] = val
                return self._check(con, assign)
            for cand in lists[i]:
                assign[others[i]] = cand
                if rec(i + 1, assign):
                    return True
            return False
        return rec(0, {})

    def _project_cmp(self, node, target, domains, want, con):
        cur = list(domains[target].values)
        if target not in con.vars or len(con.vars) != 2:
            return cur
        other = next(iter(v for v in con.vars if v != target))
        # interval path: derive integer bounds directly, keep markers exactly
        marker_cur = [v for v in cur if v is MISSING or v is NULL]
        marker_other = any(v is MISSING or v is NULL
                           for v in domains[other].values)
        iv_t = domains[target].int_bounds()
        iv_o = domains[other].int_bounds()
        if iv_t is None or iv_o is None:
            # one side has no integers left
            return marker_cur if not want else []
        tlo, thi = iv_t
        olo, ohi = iv_o
        target_is_left = self._node_mentions(node[2], target)
        ranges = self._cmp_integer_bounds(
            node[1], tlo, thi, olo, ohi, target_is_left, want, marker_other)
        if not ranges:
            return marker_cur  # no integer value survives
        kept = list(marker_cur)
        for v in cur:
            if isinstance(v, int) and not isinstance(v, bool) and \
                    any(lo <= v <= hi for lo, hi in ranges):
                kept.append(v)
        return kept

    @staticmethod
    def _node_mentions(node, name) -> bool:
        out: set = set()
        node_vars(node, out)
        return name in out

    @staticmethod
    def _cmp_integer_bounds(op, tlo, thi, olo, ohi, target_left, want,
                            marker_other):
        """Return a list of inclusive integer intervals of target values that
        have support for the comparison's truth value ([] if none)."""
        if target_left:
            a_lo, a_hi, b_lo, b_hi = tlo, thi, olo, ohi
        else:
            mirror = {"lt": "gt", "gt": "lt", "le": "ge", "ge": "le",
                      "eq": "eq", "ne": "ne"}
            op = mirror[op]
            a_lo, a_hi, b_lo, b_hi = tlo, thi, olo, ohi

        if want:
            # marker on either operand -> UNKNOWN, so integer-only support:
            if op == "ne":
                if b_lo == b_hi:
                    # every a except the singleton b
                    out = []
                    if a_lo <= b_lo - 1:
                        out.append((a_lo, b_lo - 1))
                    if b_lo + 1 <= a_hi:
                        out.append((b_lo + 1, a_hi))
                    return out
                return [(a_lo, a_hi)]
            single = {
                "lt": (a_lo, min(a_hi, b_hi - 1)),
                "le": (a_lo, min(a_hi, b_hi)),
                "gt": (max(a_lo, b_lo + 1), a_hi),
                "ge": (max(a_lo, b_lo), a_hi),
                "eq": (max(a_lo, b_lo), min(a_hi, b_hi)),
            }
            lo, hi = single[op]
            return [(lo, hi)] if lo <= hi else []

        # want False-or-UNKNOWN; markers retained separately by caller
        if marker_other:
            return [(a_lo, a_hi)]  # UNKNOWN available for every integer a
        if op == "eq":
            out = []
            if a_lo <= b_lo - 1:
                out.append((a_lo, b_lo - 1))
            if b_hi + 1 <= a_hi:
                out.append((b_hi + 1, a_hi))
            return out
        if op == "ne":
            # not(a != b) for some b means a == b; support iff interval overlap
            lo, hi = max(a_lo, b_lo), min(a_hi, b_hi)
            return [(lo, hi)] if lo <= hi else []
        false_single = {
            "lt": (max(a_lo, b_hi), a_hi),
            "le": (max(a_lo, b_hi + 1), a_hi),
            "gt": (a_lo, min(a_hi, b_lo)),
            "ge": (a_lo, min(a_hi, b_lo - 1)),
        }
        lo, hi = false_single[op]
        return [(lo, hi)] if lo <= hi else []

    def _project_eqval(self, con, target, domains):
        x, y = con.nodes
        cur = list(domains[target].values)
        if target not in con.vars:
            return cur
        others = [v for v in con.vars if v != target]
        product = 1
        for v in others:
            product *= len(domains[v])
        # Exact per-value check whenever the support product is bounded.
        if len(cur) * product <= SMALL_LIMIT:
            lists = [list(domains[v].values) for v in others]
            kept = []
            for val in cur:
                if self._has_support_values(con, target, val, others, lists):
                    kept.append(val)
            return kept
        # When every other variable is already singular, the check is linear
        # in the target domain regardless of its size.
        if all(len(domains[v]) == 1 for v in others):
            fixed = {v: next(iter(domains[v].values)) for v in others}
            kept = []
            for val in cur:
                assign = dict(fixed)
                assign[target] = val
                ok = values_equal(evaluate(x, assign), evaluate(y, assign))
                if (ok if con.kind == "eqval" else not ok):
                    kept.append(val)
            return kept
        # Genuinely large multi-variable arc: decided exactly at leaf time.
        return cur

    def _project_arith(self, node, target, domains, con):
        # Arithmetic arcs are filtered exactly when the value product is
        # small or the other side is singular; large multi-variable arcs are
        # decided at full-assignment leaf time.
        cur = list(domains[target].values)
        if target not in con.vars:
            return cur
        others = [v for v in con.vars if v != target]
        product = 1
        for v in others:
            product *= len(domains[v])
        if len(cur) * product <= SMALL_LIMIT or \
                all(len(domains[v]) == 1 for v in others):
            lists = [list(domains[v].values) for v in others]
            kept = []
            for val in cur:
                if self._has_support_values(con, target, val, others, lists):
                    kept.append(val)
            return kept
        return cur

    def _enumerative_support(self, con, target, val, domains) -> bool:
        free = [v for v in con.vars if v != target]
        lists = [list(domains[v].values) for v in free]

        def rec(i, partial):
            if i == len(free):
                partial[target] = val
                return self._check(con, partial)
            for candidate in lists[i]:
                self._tick()
                partial[free[i]] = candidate
                if rec(i + 1, partial):
                    return True
            return False

        return rec(0, {})

    def _check(self, con: Constraint, assign) -> bool:
        kind = con.kind
        if kind == "true":
            return evaluate(con.nodes[0], assign) is True
        if kind == "false":
            v = evaluate(con.nodes[0], assign)
            return v is False or v is None
        if kind == "eqval":
            return values_equal(evaluate(con.nodes[0], assign), evaluate(con.nodes[1], assign))
        if kind == "neval":
            return not values_equal(evaluate(con.nodes[0], assign), evaluate(con.nodes[1], assign))
        raise AssertionError(kind)  # pragma: no cover

    @staticmethod
    def _single_root(node) -> Optional[str]:
        if node[0] == "v":
            return node[1]
        if node[0] == "width":
            return FiniteDomainSolver._single_root(node[2])
        return None
