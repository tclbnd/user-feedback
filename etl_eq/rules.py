"""Rule format, parsing and type inference.

A rule is a JSON document with an input schema and an ordered list of
statements.  Expressions are tagged JSON objects; every tag maps to exactly
one node kind below.  Parsing produces immutable tuple nodes with a resolved
:class:`~etl_eq.semantics.IntType` on every integer-bearing node, so both the
concrete interpreter and the symbolic engine share one type inference.

Expression vocabulary
----------------------
``{"integer": N}``                 integer literal
``{"field": "x"}``                 input or local variable read
``{"add"|"sub"|"mul"|"div"|"rem": [a, b]}``
                                   fixed-width binary arithmetic; optional
                                   sibling keys ``bits``/``signed``/``overflow``
                                   force the result type
``{"width": e, "bits": n, ...}``   explicit width/signedness cast
``{"neg": e}``                     unary negation
``{"eq"|"ne"|"lt"|"le"|"gt"|"ge": [a,b]}``
                                   comparison -> BOOL (marker-safe)
``{"is_missing"|"is_null": "x"}``  absence tests -> BOOL
``{"coalesce": [a,b]}``            a if a is neither NULL nor MISSING else b
``{"fill_missing": [a,b]}``        like coalesce but only fills MISSING
``{"and"|"or": [a,b]}``            three-valued boolean logic (SQL style)
``{"not": a}``                     three-valued negation
``{"if": [c, t, e]}``              strict conditional
``{"tag": a}``                     BOOL tag (see semantics of markers)
``{"tite": [c,t,e]}``              marker-preserving conditional on a tag
``{"teq": [a,b]}``                 marker-aware equality -> tag

Statements
----------
``{"assign": "v", "expr": ...}``
``{"emit": "out", "expr": ...}``
``{"if": [c, then-stmts, else-stmts]}``
``{"error": "CODE"}``
``{"loop": {"var":"i","from":N,"times":K,"body":[...]}}``
                                   statically bounded, unrolled K iterations
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .semantics import IntType, MISSING, NULL, UnsupportedRule

BOOL = "BOOL"
ERROR = "ERROR"

ARITH_OPS = {"add", "sub", "mul", "div", "rem"}
CMP_OPS = {"eq", "ne", "lt", "le", "gt", "ge"}
LOGIC_OPS = {"and", "or"}

# node shapes:
#  ("lit", value, IntType)
#  ("var", name, IntType)
#  ("width", IntType, inner)
#  ("neg", IntType, inner)
#  ("arith", op, IntType, left, right)
#  ("cmp", op, left, right)                 -> BOOL
#  ("is_missing"|"is_null", name)           -> BOOL
#  ("coalesce"|"fill_missing", a, b)
#  ("logic", op, a, b)                      -> BOOL
#  ("not", a)                               -> BOOL
#  ("ite"|"tite", cond, then, other)
#  ("tag", a)                               -> BOOL
#  ("teq", a, b)                            -> BOOL
#  ("err-expr", code)                       -> ERROR
#
# statements:
#  ("assign", name, expr)
#  ("emit", name, expr, location)
#  ("error", code, location)
#  ("if", cond, then, else, location)
#  ("loop", var, start, times, body, location)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: IntType
    nullable: bool
    optional: bool

    def domain_markers(self) -> Tuple[Any, ...]:
        out = []
        if self.optional:
            out.append(MISSING)
        if self.nullable:
            out.append(NULL)
        return tuple(out)


@dataclass(frozen=True)
class Schema:
    fields: Tuple[FieldSpec, ...]

    def by_name(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise UnsupportedRule(f"unknown field {name!r}")

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(f.name for f in self.fields)


@dataclass(frozen=True)
class Rule:
    schema: Schema
    statements: tuple
    locations: Tuple[str, ...] = ()


class Parser:
    def __init__(self, schema_json: Dict[str, Any]):
        fields = []
        for name, spec in schema_json.items():
            fields.append(
                FieldSpec(
                    name=name,
                    type=IntType(
                        bits=spec.get("bits", 32),
                        signed=spec.get("signed", True),
                        overflow=spec.get("overflow", "wrap"),
                    ),
                    nullable=spec.get("nullable", False),
                    optional=spec.get("optional", False),
                )
            )
        self.schema = Schema(tuple(fields))
        self.var_types: Dict[str, IntType] = {f.name: f.type for f in fields}
        self.loop_vars: Dict[str, IntType] = {}
        self.locations: List[str] = []
        self._branch_counter = 0
        self._loop_counter = 0

    # -- types ------------------------------------------------------------
    def _override(self, obj: Dict[str, Any]) -> Optional[IntType]:
        if "bits" in obj or "signed" in obj or "overflow" in obj:
            return IntType(
                bits=obj.get("bits", 32),
                signed=obj.get("signed", True),
                overflow=obj.get("overflow", "wrap"),
            )
        return None

    @staticmethod
    def _literal_type(v: int) -> IntType:
        for bits in (4, 8, 10, 12, 14, 16, 32, 64):
            if -(2 ** (bits - 1)) <= v <= 2 ** (bits - 1) - 1:
                return IntType(bits, True, "wrap")
        raise UnsupportedRule(f"literal {v} does not fit any supported width")

    @staticmethod
    def _common_type(a: IntType, b: IntType, override: Optional[IntType]) -> IntType:
        if override is not None:
            return override
        bits = max(a.bits, b.bits)
        signed = a.signed and b.signed
        overflow = a.overflow if a.overflow == b.overflow else "wrap"
        return IntType(bits, signed, overflow)

    def _coerce(self, node: tuple, target: IntType) -> tuple:
        if self.node_type(node) == target:
            return node
        return ("width", target, node)

    @staticmethod
    def node_type(node: tuple):
        tag = node[0]
        if tag in ("lit", "var", "width", "neg", "arith"):
            return node[2]
        if tag in ("cmp", "logic", "not", "is_missing", "is_null", "tag", "teq"):
            return BOOL
        if tag in ("ite", "tite", "coalesce", "fill_missing"):
            return Parser.node_type(node[2])
        if tag == "err-expr":
            return ERROR
        raise UnsupportedRule(f"no type for node {tag}")

    # -- expressions ------------------------------------------------------
    _TYPE_KEYS = frozenset(("bits", "signed", "overflow"))

    def parse_expr(self, obj: Any) -> tuple:
        if isinstance(obj, bool):
            raise UnsupportedRule("bare booleans are not part of the language")
        if isinstance(obj, int):
            return ("lit", obj, self._literal_type(obj))
        if not isinstance(obj, dict) or not obj:
            raise UnsupportedRule(f"bad expression {obj!r}")
        # Tagged node with optional type-override sibling keys
        # (bits/signed/overflow); exactly one semantic tag is allowed.
        semantic = [k for k in obj if k not in self._TYPE_KEYS]
        if len(semantic) != 1:
            raise UnsupportedRule(
                f"expression needs exactly one tag, got {sorted(obj)}")
        tag = semantic[0]
        payload = obj[tag]

        if tag == "integer":
            if not isinstance(payload, int):
                raise UnsupportedRule("integer literal must be an int")
            return ("lit", payload, self._literal_type(payload))
        if tag == "field":
            if payload not in self.var_types and payload not in self.loop_vars:
                raise UnsupportedRule(f"unknown variable {payload!r}")
            return ("var", payload, self.var_types.get(payload) or self.loop_vars[payload])
        if tag in ARITH_OPS:
            if not isinstance(payload, list) or len(payload) != 2:
                raise UnsupportedRule(f"{tag} expects two operands")
            left = self.parse_expr(payload[0])
            right = self.parse_expr(payload[1])
            rtype = self._common_type(
                self.node_type(left), self.node_type(right), self._override(obj)
            )
            return ("arith", tag, rtype, self._coerce(left, rtype), self._coerce(right, rtype))
        if tag == "width":
            inner = self.parse_expr(payload)
            rtype = self._override(obj) or self.node_type(inner)
            return ("width", rtype, self._coerce(inner, rtype))
        if tag == "neg":
            inner = self.parse_expr(payload)
            rtype = self.node_type(inner)
            return ("neg", rtype, self._coerce(inner, rtype))
        if tag in CMP_OPS:
            if not isinstance(payload, list) or len(payload) != 2:
                raise UnsupportedRule(f"{tag} expects two operands")
            left, right = self.parse_expr(payload[0]), self.parse_expr(payload[1])
            ct = self._common_type(self.node_type(left), self.node_type(right), None)
            return ("cmp", tag, self._coerce(left, ct), self._coerce(right, ct))
        if tag in ("is_missing", "is_null"):
            if payload not in self.var_types and payload not in self.loop_vars:
                raise UnsupportedRule(f"absence test on unknown variable {payload!r}")
            return (tag, payload)
        if tag in ("coalesce", "fill_missing"):
            if not isinstance(payload, list) or len(payload) != 2:
                raise UnsupportedRule(f"{tag} expects two operands")
            a, b = self.parse_expr(payload[0]), self.parse_expr(payload[1])
            if self.node_type(a) != self.node_type(b):
                ct = self._common_type(self.node_type(a), self.node_type(b), None)
                a, b = self._coerce(a, ct), self._coerce(b, ct)
            return (tag, a, b)
        if tag in LOGIC_OPS:
            if not isinstance(payload, list) or len(payload) != 2:
                raise UnsupportedRule(f"{tag} expects two operands")
            return ("logic", tag, self.parse_expr(payload[0]), self.parse_expr(payload[1]))
        if tag == "not":
            return ("not", self.parse_expr(payload))
        if tag == "if":
            return self._parse_if(payload, marker_preserving=False)
        if tag == "tite":
            return self._parse_if(payload, marker_preserving=True)
        if tag == "tag":
            return ("tag", self.parse_expr(payload))
        if tag == "teq":
            if not isinstance(payload, list) or len(payload) != 2:
                raise UnsupportedRule("teq expects two operands")
            a, b = self.parse_expr(payload[0]), self.parse_expr(payload[1])
            ct = self._common_type(self.node_type(a), self.node_type(b), None)
            return ("teq", self._coerce(a, ct), self._coerce(b, ct))
        if tag == "error":
            return ("err-expr", str(payload))
        raise UnsupportedRule(f"unknown expression tag {tag!r}")

    def _parse_if(self, payload: list, marker_preserving: bool) -> tuple:
        if not isinstance(payload, list) or len(payload) != 3:
            raise UnsupportedRule("if expects [cond, then, else]")
        cond = self.parse_expr(payload[0])
        if self.node_type(cond) != BOOL:
            raise UnsupportedRule("if condition must be boolean")
        then, other = self.parse_expr(payload[1]), self.parse_expr(payload[2])
        tt, ot = self.node_type(then), self.node_type(other)
        if tt != ot and tt != ERROR and ot != ERROR and tt != BOOL and ot != BOOL:
            ct = self._common_type(tt, ot, None)
            then, other = self._coerce(then, ct), self._coerce(other, ct)
        return ("tite" if marker_preserving else "ite", cond, then, other)

    # -- statements -------------------------------------------------------
    def parse_statements(self, items: List[Any], prefix: str) -> tuple:
        return tuple(self.parse_statement(item, prefix) for item in items)

    def parse_statement(self, obj: Dict[str, Any], prefix: str) -> tuple:
        if not isinstance(obj, dict) or not obj:
            raise UnsupportedRule(f"bad statement {obj!r}")
        # Multi-key statement forms: {"emit": name, "expr": ...} and
        # {"assign": name, "expr": ...}.  Everything else is a single tagged
        # object.
        if "emit" in obj and "expr" in obj:
            name = obj["emit"]
            expr = self.parse_expr(obj["expr"])
            return ("emit", name, expr, f"{prefix}emit:{name}")
        if "assign" in obj and "expr" in obj:
            name = obj["assign"]
            expr = self.parse_expr(obj["expr"])
            t = self.node_type(expr)
            if t == ERROR:
                raise UnsupportedRule("cannot assign an error expression directly")
            self.var_types[name] = t if t != BOOL else IntType(8, True, "wrap")
            return ("assign", name, expr)
        if len(obj) != 1:
            raise UnsupportedRule(f"bad statement {obj!r}")
        (tag, payload), = obj.items()
        if tag == "assign":
            name = payload["assign"]
            expr = self.parse_expr(payload["expr"])
            t = self.node_type(expr)
            if t == ERROR:
                raise UnsupportedRule("cannot assign an error expression directly")
            self.var_types[name] = t if t != BOOL else IntType(8, True, "wrap")
            return ("assign", name, expr)
        if tag == "emit":
            name = payload["emit"]
            expr = self.parse_expr(payload["expr"])
            return ("emit", name, expr, f"{prefix}emit:{name}")
        if tag == "error":
            loc = self._new_location(prefix)
            return ("error", str(payload), loc)
        if tag == "if":
            if not isinstance(payload, list) or len(payload) != 3:
                raise UnsupportedRule("if statement expects [cond, then, else]")
            cond = self.parse_expr(payload[0])
            if self.node_type(cond) != BOOL:
                raise UnsupportedRule("if condition must be boolean")
            loc = self._new_location(prefix)
            then = self.parse_statements(payload[1], loc + ":T:")
            other = self.parse_statements(payload[2], loc + ":E:")
            return ("if", cond, then, other, loc)
        if tag == "loop":
            return self._parse_loop(payload, prefix)
        raise UnsupportedRule(f"unknown statement tag {tag!r}")

    def _new_location(self, prefix: str) -> str:
        self._branch_counter += 1
        loc = f"{prefix}b{self._branch_counter:03d}"
        self.locations.append(loc)
        return loc

    def _parse_loop(self, payload: Dict[str, Any], prefix: str) -> tuple:
        var, start, times = payload["var"], payload.get("from", 0), payload["times"]
        if not isinstance(times, int) or not 0 <= times <= 64:
            raise UnsupportedRule("loop times must be a static integer in [0,64]")
        if not isinstance(start, int):
            raise UnsupportedRule("loop from must be a static integer")
        self._loop_counter += 1
        loop_id = f"{prefix}loop{self._loop_counter:02d}"
        self.locations.append(loop_id)
        if times:
            idx_type = self._literal_type(max(abs(start), abs(start + times - 1)))
        else:
            idx_type = IntType(8, True)
        saved = dict(self.loop_vars)
        self.loop_vars[var] = idx_type
        body = self.parse_statements(payload["body"], loop_id + ":")
        self.loop_vars = saved
        return ("loop", var, start, times, body, loop_id)

    def output_to_statements(self, output: Dict[str, Any]) -> tuple:
        stmts = []
        for name, expr_obj in output.items():
            stmts.append(("emit", name, self.parse_expr(expr_obj), f"root:emit:{name}"))
        return tuple(stmts)


def parse_rule(doc: Dict[str, Any], schema: Optional[Schema] = None) -> Rule:
    """Parse a single rule document carrying its own ``input`` schema."""
    if "input" not in doc:
        raise UnsupportedRule("rule document lacks an 'input' schema")
    parser = Parser(doc["input"])
    return Rule(
        parser.schema,
        _rule_body(parser, doc),
        tuple(parser.locations),
    )


def parse_case(doc: Dict[str, Any]) -> Tuple[Rule, Rule]:
    """Parse a full case document with shared ``input`` and old/new sections."""
    parser_old, parser_new = Parser(doc["input"]), Parser(doc["input"])
    return (
        Rule(parser_old.schema, _rule_body(parser_old, doc["old"]), tuple(parser_old.locations)),
        Rule(parser_new.schema, _rule_body(parser_new, doc["new"]), tuple(parser_new.locations)),
    )


def _rule_body(parser: Parser, section: Dict[str, Any]) -> tuple:
    if "statements" in section:
        return parser.parse_statements(section["statements"], "root:")
    if "output" in section:
        return parser.output_to_statements(section["output"])
    raise UnsupportedRule("rule section needs 'output' or 'statements'")
