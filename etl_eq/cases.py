"""Synthetic rule cases used by the one-command acceptance suite.

Every case is generated as plain rule JSON so it exercises the same parser,
type inference and interpreter as user-supplied rules.  Cases deliberately
place differences at the exact boundaries the brief calls out:

* ``overflow_edge``  -- versions agree except at the fixed-width wrap edge;
* ``null_branch``    -- versions agree except on the NULL branch, keeping
                        MISSING handling identical;
* ``loop_last_iter`` -- a statically bounded loop whose divergence appears
                        only on the final iteration;
* ``div_zero``       -- divide-by-zero classification difference;
* ``equivalent_*``   -- pairs that must be proven equivalent over the whole
                        domain (including all boundary rows).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Tuple

from .semantics import NULL


def _int(bits: int, signed=True, overflow="wrap") -> Dict[str, Any]:
    return {"bits": bits, "signed": signed, "overflow": overflow}


def small_schema(bits: int = 4) -> Dict[str, Any]:
    return {
        "a": {**_int(bits), "nullable": True, "optional": True},
        "b": {**_int(bits), "nullable": True, "optional": False},
    }


# ---------------------------------------------------------------------------
# 1. Overflow-edge difference (wrap arithmetic)
# ---------------------------------------------------------------------------

def overflow_edge_case(bits: int = 4) -> Dict[str, Any]:
    top = 2 ** (bits - 1) - 1
    return {
        "input": small_schema(bits),
        "old": {
            "output": {
                "z": {
                    "if": [
                        {"is_missing": "a"},
                        {"error": "missing-a"},
                        {
                            "if": [
                                {"is_null": "a"},
                                {"error": "null-a"},
                                {"add": [{"field": "a"}, {"integer": 1}],
                                 "bits": bits},
                            ]
                        },
                    ]
                }
            }
        },
        "new": {
            "output": {
                # saturating replacement: differs only at the wrap boundary
                "z": {
                    "if": [
                        {"is_missing": "a"},
                        {"error": "missing-a"},
                        {
                            "if": [
                                {"is_null": "a"},
                                {"error": "null-a"},
                                {"add": [{"field": "a"}, {"integer": 1}],
                                 "bits": bits, "signed": True,
                                 "overflow": "saturate"},
                            ]
                        },
                    ]
                }
            }
        },
        "_expect": {"kind": "field-value", "trigger": {"a": top, "b": 0}},
    }


# ---------------------------------------------------------------------------
# 2. NULL-only branch difference (MISSING identical)
# ---------------------------------------------------------------------------

def null_branch_case(bits: int = 4) -> Dict[str, Any]:
    base = {
        "if": [
            {"is_missing": "a"},
            {"error": "missing-a"},
            {"if": [
                {"is_null": "a"},
                None,  # filled per version below
                {"add": [{"field": "a"}, {"integer": 2}], "bits": bits},
            ]},
        ]
    }
    old = copy.deepcopy(base)
    old["if"][2]["if"][1] = {"error": "null-a"}
    new = copy.deepcopy(base)
    new["if"][2]["if"][1] = {"integer": 0}
    return {
        "input": small_schema(bits),
        "old": {"output": {"z": old}},
        "new": {"output": {"z": new}},
        "_expect": {"kind": "error-classification", "trigger": {"a": NULL, "b": 0}},
    }


# ---------------------------------------------------------------------------
# 3. Loop whose difference appears only on the last iteration
# ---------------------------------------------------------------------------

def loop_last_iter_case(bits: int = 4, times: int = 4) -> Dict[str, Any]:
    schema = {"a": {**_int(bits), "nullable": False, "optional": False}}

    def rule(guard_last: bool):
        body = [
            {"assign": "acc",
             "expr": {"add": [{"field": "acc"}, {"integer": 1}], "bits": bits}},
        ]
        if guard_last:
            # new version skips the final increment on a == max
            body = [
                {"if": [
                    {"and": [
                        {"eq": [{"field": "i"}, {"integer": times - 1}]},
                        {"eq": [{"field": "a"},
                                {"integer": 2 ** (bits - 1) - 1}]},
                    ]},
                    [],
                    [{"assign": "acc",
                      "expr": {"add": [{"field": "acc"}, {"integer": 1}],
                               "bits": bits}}],
                ]}
            ]
        return {
            "statements": [
                {"assign": "acc", "expr": {"integer": 0}},
                {"loop": {"var": "i", "from": 0, "times": times, "body": body}},
                {"emit": "z", "expr": {"field": "acc"}},
            ]
        }

    return {
        "input": schema,
        "old": rule(False),
        "new": rule(True),
        "_expect": {
            "kind": "field-value",
            "trigger": {"a": 2 ** (bits - 1) - 1},
        },
    }


# ---------------------------------------------------------------------------
# 4. Divide-by-zero classification difference
# ---------------------------------------------------------------------------

def div_zero_case(bits: int = 4) -> Dict[str, Any]:
    schema = {
        "a": {**_int(bits), "nullable": False, "optional": False},
        "b": {**_int(bits), "nullable": False, "optional": False},
    }
    return {
        "input": schema,
        "old": {
            "statements": [
                {"emit": "q",
                 "expr": {"div": [{"field": "a"}, {"field": "b"}], "bits": bits}}
            ]
        },
        "new": {
            "statements": [
                {"if": [
                    {"eq": [{"field": "b"}, {"integer": 0}]},
                    [{"error": "custom-zero-guard"}],
                    [{"emit": "q",
                      "expr": {"div": [{"field": "a"}, {"field": "b"}],
                               "bits": bits}}],
                ]}
            ]
        },
        "_expect": {"kind": "error-classification", "trigger": {"a": 1, "b": 0}},
    }


# ---------------------------------------------------------------------------
# 5. Equivalent pairs (must be proven equal over the complete domain)
# ---------------------------------------------------------------------------

def equivalent_arith_case(bits: int = 4) -> Dict[str, Any]:
    return {
        "input": small_schema(bits),
        "old": {"output": {"z": {"add": [{"field": "a"}, {"field": "b"}],
                                  "bits": bits}}},
        "new": {"output": {"z": {"add": [{"field": "b"}, {"field": "a"}],
                                  "bits": bits}}},
        "_expect": {"kind": "equivalent"},
    }


def equivalent_branch_case(bits: int = 4) -> Dict[str, Any]:
    # two formulations of the same marker-aware normalization
    old_expr = {
        "if": [
            {"is_missing": "a"},
            {"integer": 0},
            {"if": [
                {"is_null": "a"},
                {"integer": 0},
                {"add": [{"field": "a"}, {"integer": 1}], "bits": bits},
            ]},
        ]
    }
    new_expr = {
        "coalesce": [
            {"add": [{"field": "a"}, {"integer": 1}], "bits": bits},
            {"integer": 0},
        ]
    }
    return {
        "input": small_schema(bits),
        "old": {"output": {"z": old_expr}},
        "new": {"output": {"z": new_expr}},
        "_expect": {"kind": "equivalent"},
    }


def equivalent_loop_case(bits: int = 4, times: int = 3) -> Dict[str, Any]:
    schema = {"a": {**_int(bits), "nullable": False, "optional": False}}
    loop_rule = {
        "statements": [
            {"assign": "acc", "expr": {"integer": 0}},
            {"loop": {
                "var": "i", "from": 0, "times": times,
                "body": [{"assign": "acc",
                          "expr": {"add": [{"field": "acc"}, {"integer": 1}],
                                   "bits": 8}}],
            }},
            {"emit": "z", "expr": {"field": "acc"}},
        ]
    }
    straight_rule = {
        "statements": [
            {"emit": "z", "expr": {"integer": times}},
        ]
    }
    return {
        "input": schema,
        "old": loop_rule,
        "new": straight_rule,
        "_expect": {"kind": "equivalent"},
    }


# ---------------------------------------------------------------------------
# 6. Multi-layer branch merge stress case (wide domain)
# ---------------------------------------------------------------------------

def branch_stress_case(bits: int = 12) -> Dict[str, Any]:
    """Four nested if layers over two wide inputs; versions differ only in
    one leaf classification, exercising pruning and confluence merging."""
    schema = {
        "a": {**_int(bits), "nullable": True, "optional": True},
        "b": {**_int(bits), "nullable": True, "optional": False},
    }

    def leaf(code):
        return {"error": code}

    def tree(deep_code):
        return {
            "if": [
                {"is_missing": "a"},
                leaf("missing-a"),
                {"if": [
                    {"is_null": "a"},
                    leaf("null-a"),
                    {"if": [
                        {"gt": [{"field": "a"}, {"integer": 0}]},
                        {"if": [
                            {"ge": [{"field": "b"}, {"field": "a"}]},
                            leaf("region-1"),
                            leaf("region-2"),
                        ]},
                        {"if": [
                            {"lt": [{"field": "b"}, {"integer": 0}]},
                            leaf(deep_code),
                            leaf("region-4"),
                        ]},
                    ]},
                ]},
            ]
        }

    return {
        "input": schema,
        "old": {"output": {"z": tree("region-3")}},
        "new": {"output": {"z": tree("region-3-renamed")}},
        "_expect": {"kind": "error-classification"},
    }


ALL_CASES = [
    "overflow_edge",
    "null_branch",
    "loop_last_iter",
    "div_zero",
    "equivalent_arith",
    "equivalent_branch",
    "equivalent_loop",
]


def merge_confluence_case(bits: int = 8) -> Dict[str, Any]:
    """Feasible branches that converge on the same observable state.

    The if has two feasible branches (a positive and a non-positive region),
    and both assign ``acc`` the *same* expression.  At the join the two
    states therefore have identical environment bindings, emitted outputs and
    error status; only their path constraints differ.  The engine merges them
    after proving the union of constraints feasible.  A dead guard inside one
    branch also exercises feasible-path pruning.
    """
    schema = {"a": {**_int(bits), "nullable": False, "optional": False}}
    same_value = {"add": [{"field": "a"}, {"integer": 1}], "bits": 8}
    statements = [
        {"if": [
            {"gt": [{"field": "a"}, {"integer": 0}]},
            [
                # infeasible inner guard: a > 0 and a < 0 cannot both hold
                {"if": [
                    {"lt": [{"field": "a"}, {"integer": 0}]},
                    [{"assign": "acc", "expr": {"integer": 99}}],
                    [{"assign": "acc", "expr": same_value}],
                ]},
            ],
            [{"assign": "acc", "expr": same_value}],
        ]},
        {"emit": "z", "expr": {"field": "acc"}},
    ]
    return {
        "input": schema,
        "old": {"statements": statements},
        "new": {"statements": [
            {"emit": "z", "expr": same_value},
        ]},
        "_expect": {"kind": "equivalent"},
    }


def undecidable_case(bits: int = 16) -> Dict[str, Any]:
    """A deliberately tiny-budget probe: the report must say UNDECIDABLE
    rather than guess when the search budget is exhausted."""
    schema = {
        "a": {**_int(bits), "nullable": False, "optional": False},
        "b": {**_int(bits), "nullable": False, "optional": False},
    }
    expr = {"mul": [{"field": "a"}, {"field": "b"}], "bits": bits}
    return {
        "input": schema,
        "old": {"statements": [{"emit": "z", "expr": expr}]},
        "new": {"statements": [{"emit": "z",
                                "expr": {"add": [{"field": "a"}, {"field": "b"}],
                                         "bits": bits}}]},
        "_expect": {"kind": "budget-probe"},
    }
