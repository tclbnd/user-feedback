"""Finite-domain enumeration for independent acceptance checking.

The enumerator is deliberately independent of both the symbolic engine and the
solver: it walks the full cartesian domain (missing / null / every machine
value) directly and drives the concrete interpreter.  For small domains this
gives an oracle the symbolic verdict must agree with exactly.
"""
from __future__ import annotations

import itertools
from typing import Dict, Iterator, List, Optional, Tuple

from .ast import FieldSpec
from .solver.interface import InputPoint


def enumerate_domain(schema: Dict[str, FieldSpec]) -> Iterator[InputPoint]:
    per_field: List[List[Tuple[str, Optional[int]]]] = []
    names = sorted(schema)
    for name in names:
        spec = schema[name]
        choices: List[Tuple[str, Optional[int]]] = []
        if spec.optional:
            choices.append(("missing", None))
        if spec.nullable:
            choices.append(("null", None))
        for v in range(spec.domain_lo(), spec.domain_hi() + 1):
            choices.append(("value", v))
        per_field.append(choices)

    for combo in itertools.product(*per_field):
        values: Dict[str, Optional[int]] = {}
        missing: List[str] = []
        for name, (kind, payload) in zip(names, combo):
            if kind == "missing":
                missing.append(name)
            elif kind == "null":
                values[name] = None
            else:
                values[name] = payload
        yield InputPoint(values=values, missing=tuple(missing))


def domain_size(schema: Dict[str, FieldSpec]) -> int:
    total = 1
    for spec in schema.values():
        per = 0
        if spec.optional:
            per += 1
        if spec.nullable:
            per += 1
        per += spec.domain_hi() - spec.domain_lo() + 1
        total *= per
    return total


def point_to_record(point: InputPoint) -> dict:
    record = dict(point.values)
    for name in point.missing:
        record.pop(name, None)
    return record
