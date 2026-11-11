"""Brute-force enumeration over the full finite input domain.

Used by the acceptance suite as an independent oracle: it compares the two
concrete interpreters' outcomes on *every* supported input, computes the
truly minimal differing record directly, and returns statistics the symbolic
checker must agree with.
"""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Tuple

from .concrete import run_rule
from .rules import Rule
from .semantics import MISSING, NULL, outcomes_equal, value_order_key


def enumerate_domain(rule: Rule):
    axes = []
    for spec in rule.schema.fields:
        values = list(range(spec.type.min, spec.type.max + 1))
        values.extend(spec.domain_markers())
        axes.append((spec.name, values))
    names = [n for n, _ in axes]
    for combo in product(*[vals for _, vals in axes]):
        yield names, dict(zip(names, combo))


def brute_force_compare(old: Rule, new: Rule) -> Dict[str, Any]:
    total = 0
    differing: List[Tuple[tuple, dict]] = []
    error_kinds: Dict[str, int] = {}
    for names, record in enumerate_domain(old):
        total += 1
        o = run_rule(old, record)
        n = run_rule(new, record)
        if o.error:
            error_kinds[o.error] = error_kinds.get(o.error, 0) + 1
        if not outcomes_equal(o, n):
            key = tuple(value_order_key(record[f]) for f in names)
            differing.append((key, record))
    differing.sort(key=lambda item: item[0])
    return {
        "domain_size": total,
        "differing_count": len(differing),
        "minimal_record": differing[0][1] if differing else None,
        "all_records": [rec for _, rec in differing],
        "old_error_kinds": error_kinds,
    }
