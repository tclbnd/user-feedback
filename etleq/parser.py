"""JSON rule format -> :mod:`etleq.ast`.

Supported surface vocabulary
----------------------------

Expressions (both rule forms share it)::

    {"integer": 7}                      constant
    {"field": "base"}                   input field read
    {"var": "acc"}                      local variable read
    {"is_missing": "base"}              presence test (input fields only)
    {"is_null": <expr>}                 NULL test
    {"not": <expr>}
    {"and": [..]}  {"or": [..]}
    {"eq"|"ne"|"lt"|"le"|"gt"|"ge": [l, r]}
    {"add"|"sub"|"mul"|"div"|"rem": [l, r]}
    {"wrap": {"arg": <expr>, "bits": 16}}   explicit width reduction
    {"if": [cond, then, else]}          three-valued cond; NULL cond -> E_NULL_BRANCH
    {"error": "code"}                   explicit failure

Statement-form blocks add::

    {"let": name, "value": expr}  {"set": name, "value": expr}
    {"if": cond, "then": [..], "else": [..]}
    {"loop": {"bound": expr, "max": 16, "index": "i",
              "init": {acc: expr, ...}, "body": [..]}}
    {"emit": name, "value": expr}
    {"fail": "code"}

Anything outside this vocabulary raises :class:`UnsupportedRule`; the
equivalence driver turns that into an *unknown* verdict rather than guessing.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from . import ast
from .ast import (
    EAnd, EArith, ECmp, EError, EField, EIf, EInt, EMissing, ENot, ENullTest,
    EOr, EVar, EWrap, FieldSpec, OutputSpec, Rule, SIf, SLet, SLoop, SSet,
    SEmit, SFail, Stmt,
)

ARITH_KEYS = set(ast.ARITH_OPS)
CMP_KEYS = set(ast.CMP_OPS)
BOOL_KEYS = {"is_missing", "is_null", "not", "and", "or"} | CMP_KEYS
VALUE_KEYS = {"integer", "field", "var", "wrap", "if", "error"} | ARITH_KEYS
SUPPORTED_KEYS = BOOL_KEYS | VALUE_KEYS
STMT_KEYS = {"let", "set", "if", "loop", "emit", "fail"}


class UnsupportedRule(Exception):
    """Raised when a rule uses vocabulary outside the finite semantics."""


class RuleFormatError(Exception):
    """Raised on malformed but syntactically recognised rules."""


# ---------------------------------------------------------------------------
# Location assignment (stable document preorder)
# ---------------------------------------------------------------------------

class Locator:
    def __init__(self, prefix: str):
        self._n = 0
        self.prefix = prefix

    def next(self) -> str:
        self._n += 1
        return f"{self.prefix}L{self._n}"


# ---------------------------------------------------------------------------
# Expression parsing
# ---------------------------------------------------------------------------

def _parse_expr(node: Any, loc: Locator) -> Expr:
    if not isinstance(node, dict) or len(node) != 1:
        raise RuleFormatError(f"expression must be a single-key object, got {node!r}")
    (key, payload), = node.items()
    here = loc.next()

    if key == "integer":
        if not isinstance(payload, int) or isinstance(payload, bool):
            raise RuleFormatError("integer payload must be an int")
        return EInt(loc=here, bits=None, value=payload)
    if key == "field":
        return EField(loc=here, bits=None, name=payload)
    if key == "var":
        return EVar(loc=here, bits=None, name=payload)
    if key == "is_missing":
        if not isinstance(payload, str):
            raise RuleFormatError("is_missing takes a field name string")
        return EMissing(loc=here, bits=None, name=payload)
    if key == "is_null":
        # Both {"is_null": "base"} (field-name shorthand, matching is_missing)
        # and {"is_null": <expr>} are accepted.
        if isinstance(payload, str):
            ref = EField(loc=loc.next(), bits=None, name=payload)
        else:
            ref = _parse_expr(payload, loc)
        return ENullTest(loc=here, bits=None, ref=ref)
    if key == "not":
        return ENot(loc=here, bits=None, arg=_parse_expr(payload, loc))
    if key in ("and", "or"):
        items = tuple(_parse_expr(x, loc) for x in _as_list(payload))
        ctor = EAnd if key == "and" else EOr
        return ctor(loc=here, bits=None, items=items)
    if key in CMP_KEYS:
        l, r = _as_pair(payload)
        return ECmp(loc=here, bits=None, op=key,
                    left=_parse_expr(l, loc), right=_parse_expr(r, loc))
    if key in ARITH_KEYS:
        l, r = _as_pair(payload)
        return EArith(loc=here, bits=None, op=key,
                      left=_parse_expr(l, loc), right=_parse_expr(r, loc))
    if key == "wrap":
        if not isinstance(payload, dict) or "arg" not in payload:
            raise RuleFormatError("wrap needs {arg, bits}")
        bits = payload.get("bits")
        if bits not in (8, 16, 32, 64):
            raise RuleFormatError("wrap bits must be 8/16/32/64")
        return EWrap(loc=here, bits=int(bits), arg=_parse_expr(payload["arg"], loc))
    if key == "if":
        parts = _as_list(payload)
        if len(parts) != 3:
            raise RuleFormatError("if needs [cond, then, else]")
        return EIf(loc=here, bits=None,
                   cond=_parse_expr(parts[0], loc),
                   then=_parse_expr(parts[1], loc),
                   els=_parse_expr(parts[2], loc))
    if key == "error":
        if not isinstance(payload, str) or not payload:
            raise RuleFormatError("error needs a non-empty code")
        return EError(loc=here, bits=None, code=payload)

    raise UnsupportedRule(f"unsupported expression form {key!r}")


def _as_list(payload) -> list:
    if not isinstance(payload, list):
        raise RuleFormatError(f"expected list, got {type(payload).__name__}")
    return payload


def _as_pair(payload):
    items = _as_list(payload)
    if len(items) != 2:
        raise RuleFormatError("binary operator needs exactly two operands")
    return items[0], items[1]


# ---------------------------------------------------------------------------
# Statement parsing (statement form)
# ---------------------------------------------------------------------------

def _parse_stmt(node: Any, loc: Locator) -> Stmt:
    if not isinstance(node, dict) or len(node) == 0:
        raise RuleFormatError("statement must be a non-empty object")
    key = next(iter(node))
    if key not in STMT_KEYS:
        raise UnsupportedRule(f"unsupported statement form {key!r}")
    payload = node[key]
    here = loc.next()

    if key in ("let", "set"):
        ctor = SLet if key == "let" else SSet
        if not isinstance(payload, dict) or "value" not in payload:
            raise RuleFormatError(f"{key} needs {{name-ish, value}}")
        name = payload.get("name")
        if not isinstance(name, str):
            raise RuleFormatError(f"{key} needs a string name")
        return ctor(loc=here, name=name, value=_parse_expr(payload["value"], loc))
    if key == "if":
        if not isinstance(payload, dict):
            raise RuleFormatError("if statement needs an object")
        then = tuple(_parse_stmt(s, loc) for s in payload.get("then", []))
        els = tuple(_parse_stmt(s, loc) for s in payload.get("else", []))
        return SIf(loc=here, cond=_parse_expr(payload["cond"], loc),
                   then=then, els=els)
    if key == "loop":
        return _parse_loop(here, payload, loc)
    if key == "emit":
        if not isinstance(payload, dict):
            raise RuleFormatError("emit needs an object")
        return SEmit(loc=here, output=payload["output"],
                     value=_parse_expr(payload["value"], loc))
    if key == "fail":
        if not isinstance(payload, str):
            raise RuleFormatError("fail needs a code string")
        return SFail(loc=here, code=payload)
    raise UnsupportedRule(key)  # pragma: no cover - guarded above


def _parse_loop(here: str, payload: dict, loc: Locator) -> SLoop:
    if not isinstance(payload, dict):
        raise RuleFormatError("loop needs an object")
    bound = _parse_expr(payload["bound"], loc)
    max_bound = int(payload.get("max", 16))
    if max_bound < 0 or max_bound > 4096:
        raise RuleFormatError("loop max out of range")
    index = payload.get("index", "i")
    init_raw = payload.get("init", {})
    if not isinstance(init_raw, dict):
        raise RuleFormatError("loop init must be an object")
    inits = tuple(
        (name, _parse_expr(expr, loc)) for name, expr in init_raw.items()
    )
    body = tuple(_parse_stmt(s, loc) for s in _as_list(payload.get("body", [])))
    return SLoop(loc=here, bound=bound, max_bound=max_bound, index=index,
                 inits=inits, body=body)


# ---------------------------------------------------------------------------
# Width resolution
# ---------------------------------------------------------------------------

def _resolve_widths(expr: Expr, schema: Dict[str, FieldSpec],
                    local_widths: Dict[str, int], hint: Optional[int]) -> Expr:
    if isinstance(expr, EInt):
        return replace(expr, bits=hint if hint is not None else 32)
    if isinstance(expr, EField):
        if expr.name not in schema:
            raise RuleFormatError(f"unknown input field {expr.name!r}")
        return replace(expr, bits=schema[expr.name].bits)
    if isinstance(expr, EVar):
        if expr.name in local_widths:
            return replace(expr, bits=local_widths[expr.name])
        if expr.name in schema:
            return replace(expr, bits=schema[expr.name].bits)
        raise RuleFormatError(f"unknown local variable {expr.name!r}")
    if isinstance(expr, (EMissing, ENot, EAnd, EOr, ECmp, EError)):
        return _resolve_children(expr, schema, local_widths, hint)
    if isinstance(expr, ENullTest):
        ref = _resolve_widths(expr.ref, schema, local_widths, hint)
        return replace(expr, ref=ref)
    if isinstance(expr, EArith):
        left = _resolve_widths(expr.left, schema, local_widths, hint)
        right = _resolve_widths(expr.right, schema, local_widths, hint)
        # Width reconciliation: an unhinted integer literal adopts the other
        # operand's machine width. Real cross-width field/variable mixes still
        # require an explicit wrap and keep raising.
        if left.bits != right.bits:
            if isinstance(expr.left, EInt):
                left = replace(left, bits=right.bits)
            elif isinstance(expr.right, EInt):
                right = replace(right, bits=left.bits)
        if left.bits != right.bits:
            raise RuleFormatError(
                f"arith width mismatch {left.bits} vs {right.bits} at {expr.loc}; "
                "use wrap to convert widths explicitly")
        return replace(expr, left=left, right=right, bits=left.bits)
    if isinstance(expr, EWrap):
        arg = _resolve_widths(expr.arg, schema, local_widths, None)
        return replace(expr, arg=arg)
    if isinstance(expr, EIf):
        cond = _resolve_widths(expr.cond, schema, local_widths, None)
        then = _resolve_widths(expr.then, schema, local_widths, hint)
        # An error branch carries no machine width; inherit the other side.
        if isinstance(expr.then, EError):
            then = replace(then, bits=expr.els.bits or hint)
        els = _resolve_widths(expr.els, schema, local_widths, then.bits)
        if isinstance(expr.els, EError):
            els = replace(els, bits=then.bits)
        if then.bits is not None and els.bits is not None and then.bits != els.bits:
            raise RuleFormatError(f"if branch width mismatch at {expr.loc}")
        return replace(expr, cond=cond, then=then, els=els,
                       bits=then.bits if then.bits is not None else els.bits)
    raise UnsupportedRule(f"cannot resolve widths for {type(expr).__name__}")


def _resolve_children(expr: Expr, schema, local_widths, hint) -> Expr:
    if isinstance(expr, ENot):
        return replace(expr, arg=_resolve_widths(expr.arg, schema, local_widths, None))
    if isinstance(expr, (EAnd, EOr)):
        return replace(expr, items=tuple(
            _resolve_widths(x, schema, local_widths, None) for x in expr.items))
    if isinstance(expr, ECmp):
        left = _resolve_widths(expr.left, schema, local_widths, hint)
        right = _resolve_widths(expr.right, schema, local_widths, left.bits)
        if left.bits != right.bits:
            raise RuleFormatError(f"comparison width mismatch at {expr.loc}")
        return replace(expr, left=left, right=right)
    if isinstance(expr, EError):
        return expr
    if isinstance(expr, EMissing):
        if expr.name not in schema:
            raise RuleFormatError(f"unknown input field {expr.name!r}")
        return expr
    raise UnsupportedRule(type(expr).__name__)  # pragma: no cover


# ---------------------------------------------------------------------------
# Flatten expression-form outputs into the statement IR
# ---------------------------------------------------------------------------

class Flattener:
    """Lowers nested ``if`` / ``error`` expressions to SIf / SFail statements.

    Plain (branch-free) expressions stay inline, so simple rules have zero
    synthetic temporaries.
    """

    def __init__(self, loc: Locator, schema: Dict[str, FieldSpec],
                 local_widths: Dict[str, int]):
        self.loc = loc
        self.schema = schema
        self.local_widths = local_widths
        self._temps = 0

    def fresh(self, bits: int) -> str:
        self._temps += 1
        name = f"#t{self._temps}"
        self.local_widths[name] = bits
        return name

    def lower_value(self, e: Expr) -> Tuple[List[Stmt], Expr]:
        """Return (statements, branch-free expr) for a value expression."""
        if isinstance(e, EIf):
            bits = e.bits
            name = self.fresh(bits)
            then_stmts, then_val = self.lower_value(e.then)
            els_stmts, els_val = self.lower_value(e.els)
            # A failing branch terminates; its placeholder value is unreachable
            # and must not emit a dead assignment after the fail.
            then_tail = [] if (then_stmts and isinstance(then_stmts[-1], SFail)) \
                else [SSet(self.loc.next(), name, then_val)]
            els_tail = [] if (els_stmts and isinstance(els_stmts[-1], SFail)) \
                else [SSet(self.loc.next(), name, els_val)]
            stmts = [SIf(
                loc=self.loc.next(), cond=e.cond,
                then=tuple(then_stmts + then_tail),
                els=tuple(els_stmts + els_tail),
            )]
            return stmts, EVar(loc=e.loc, bits=bits, name=name)
        if isinstance(e, EError):
            return [SFail(loc=e.loc, code=e.code)], EInt(loc=e.loc, bits=32, value=0)
        if isinstance(e, EArith):
            ls, le = self.lower_value(e.left)
            rs, re_ = self.lower_value(e.right)
            if ls or rs:
                rebuilt = replace(e, left=le, right=re_)
                return ls + rs, rebuilt
            return [], e
        if isinstance(e, EWrap):
            ss, se = self.lower_value(e.arg)
            if ss:
                return ss, replace(e, arg=se)
            return [], e
        return [], e

    def lower_condition(self, e: Expr) -> Tuple[List[Stmt], Expr]:
        """Lower conditions, including boolean-valued if/error subterms."""
        if isinstance(e, EIf):
            # Boolean temp: represented as a width-less variable; use width 1
            # marker so local_widths stays int-keyed.
            name = self.fresh(1)
            ts, tv = self.lower_condition(e.then)
            es, ev = self.lower_condition(e.els)
            stmts = [SIf(
                loc=self.loc.next(), cond=e.cond,
                then=tuple(ts + [SSet(self.loc.next(), name, tv)]),
                els=tuple(es + [SSet(self.loc.next(), name, ev)]),
            )]
            return stmts, EVar(loc=e.loc, bits=1, name=name)
        if isinstance(e, EError):
            return [SFail(loc=e.loc, code=e.code)], e
        if isinstance(e, (ENot,)):
            ss, se = self.lower_condition(e.arg)
            return ss, (replace(e, arg=se) if ss else e)
        if isinstance(e, (EAnd, EOr)):
            out_stmts: List[Stmt] = []
            new_items = []
            for item in e.items:
                ss, se = self.lower_condition(item)
                out_stmts.extend(ss)
                new_items.append(se)
            return out_stmts, replace(e, items=tuple(new_items))
        if isinstance(e, ECmp):
            ls, le = self.lower_value(e.left)
            rs, re_ = self.lower_value(e.right)
            return ls + rs, (replace(e, left=le, right=re_) if (ls or rs) else e)
        return [], e


# ---------------------------------------------------------------------------
# Top-level rule parsing
# ---------------------------------------------------------------------------

def _parse_schema(raw: dict) -> Dict[str, FieldSpec]:
    if not isinstance(raw, dict):
        raise RuleFormatError("input schema must be an object")
    schema: Dict[str, FieldSpec] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise RuleFormatError(f"field {name!r} spec must be an object")
        bits = spec.get("bits", 32)
        if bits not in (8, 16, 32, 64):
            raise RuleFormatError("bits must be 8/16/32/64")
        schema[name] = FieldSpec(
            bits=int(bits),
            signed=bool(spec.get("signed", True)),
            nullable=bool(spec.get("nullable", False)),
            optional=bool(spec.get("optional", False)),
        )
    return schema


def _parse_output_specs(raw: dict, exprs: Dict[str, Expr]) -> Tuple[OutputSpec, ...]:
    specs: List[OutputSpec] = []
    for name in sorted(raw):  # stable order; report ordering re-applied later
        spec = raw[name]
        inferred_bits = exprs[name].bits if exprs[name].bits else 32
        if isinstance(spec, dict):
            bits = int(spec.get("bits", inferred_bits))
            nullable = bool(spec.get("nullable", True))
        else:
            bits, nullable = inferred_bits, True
        specs.append(OutputSpec(name=name, bits=bits, nullable=nullable))
    return tuple(specs)


def parse_rule(doc: dict, version: str, *, loop_bound: int = 16) -> Rule:
    """Parse one version of a rule document."""
    if not isinstance(doc, dict):
        raise RuleFormatError("rule must be an object")
    loc = Locator(prefix=version + ":")
    schema = _parse_schema(doc.get("input", {})) if "input" in doc else None

    # Input may be declared once at the case top level; caller re-injects it.
    if schema is None:
        raise RuleFormatError("rule missing input schema (caller should inject)")

    local_widths = {n: f.bits for n, f in schema.items()}
    block: List[Stmt] = []
    output_specs: List[OutputSpec] = []

    if "block" in doc:
        # statement form
        raw_outputs = doc.get("output", {})
        for raw in doc["block"]:
            block.append(_parse_stmt(raw, loc))
        for name in sorted(raw_outputs):
            spec = raw_outputs[name]
            output_specs.append(OutputSpec(
                name=name,
                bits=int(spec.get("bits", 32)) if isinstance(spec, dict) else 32,
                nullable=bool(spec.get("nullable", True)) if isinstance(spec, dict) else True,
            ))
        _register_loop_locals(block, local_widths)
        block = [_resolve_stmt(s, schema, local_widths) for s in block]
    elif "output" in doc:
        # expression form
        flat = Flattener(loc, schema, local_widths)
        raw_outputs = doc["output"]
        resolved: Dict[str, Expr] = {}
        for name in sorted(raw_outputs):
            expr = _parse_expr(raw_outputs[name], loc)
            expr = _resolve_widths(expr, schema, local_widths, None)
            resolved[name] = expr
        output_specs = list(_parse_output_specs(raw_outputs, resolved))
        for spec in output_specs:
            expr = resolved[spec.name]
            stmts, val = flat.lower_value(expr)
            block.extend(stmts)
            block.append(SEmit(loc=loc.next(), output=spec.name, value=val))
    else:
        raise RuleFormatError("rule needs either 'output' (expr form) or 'block' (stmt form)")

    return Rule(schema=schema, outputs=tuple(sorted(output_specs, key=lambda o: o.name)),
                block=tuple(block), loop_bound=loop_bound)


def _register_loop_locals(block, local_widths):
    for s in block:
        if isinstance(s, SLoop):
            for name, init in s.inits:
                local_widths.setdefault(name, init.bits or 32)
            local_widths.setdefault(s.index, 32)
            _register_loop_locals(s.body, local_widths)
        elif isinstance(s, SIf):
            _register_loop_locals(s.then, local_widths)
            _register_loop_locals(s.els, local_widths)


def _resolve_stmt(stmt: Stmt, schema, local_widths) -> Stmt:
    if isinstance(stmt, (SLet, SSet)):
        value = _resolve_widths(stmt.value, schema, local_widths,
                                local_widths.get(stmt.name))
        if isinstance(stmt, SLet):
            local_widths[stmt.name] = value.bits
        return replace(stmt, value=value)
    if isinstance(stmt, SEmit):
        spec_bits = None
        value = _resolve_widths(stmt.value, schema, local_widths, spec_bits)
        return replace(stmt, value=value)
    if isinstance(stmt, SFail):
        return stmt
    if isinstance(stmt, SIf):
        cond = _resolve_widths(stmt.cond, schema, local_widths, None)
        # Resolve both branches against snapshots so a let introduced inside
        # one branch cannot leak into the sibling or into later statements.
        then_snapshot = dict(local_widths)
        then = tuple(_resolve_stmt(s, schema, then_snapshot) for s in stmt.then)
        els_snapshot = dict(local_widths)
        els = tuple(_resolve_stmt(s, schema, els_snapshot) for s in stmt.els)
        return replace(stmt, cond=cond, then=then, els=els)
    if isinstance(stmt, SLoop):
        bound = _resolve_widths(stmt.bound, schema, local_widths, None)
        # Loop locals live in a nested scope that must not escape to the block.
        outer_snapshot = dict(local_widths)
        inner = dict(local_widths)
        for name, init in stmt.inits:
            r = _resolve_widths(init, schema, inner, None)
            inner[name] = r.bits
        inner[stmt.index] = 32
        body = tuple(_resolve_stmt(s, schema, inner) for s in stmt.body)
        local_widths.clear()
        local_widths.update(outer_snapshot)
        return replace(stmt, bound=bound,
                       inits=tuple((n, _resolve_widths(e, schema, outer_snapshot, None))
                                   for n, e in stmt.inits),
                       body=body)
    raise UnsupportedRule(type(stmt).__name__)  # pragma: no cover


def parse_case(doc: dict, *, loop_bound: int = 16) -> Tuple[Rule, Rule]:
    """Parse a whole equivalence case ``{input, old, new}``."""
    if "input" not in doc:
        raise RuleFormatError("case needs a shared 'input' schema")
    old_doc = dict(doc["old"]); old_doc["input"] = doc["input"]
    new_doc = dict(doc["new"]); new_doc["input"] = doc["input"]
    return (parse_rule(old_doc, "old", loop_bound=loop_bound),
            parse_rule(new_doc, "new", loop_bound=loop_bound))
