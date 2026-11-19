"""jsonb input and output.

Input is JSON text; output is jsonb's text form.  Numbers are kept as
Decimal; object keys are unique.
"""
import json
from decimal import Decimal


class JsonbError(Exception):
    pass


def parse(text):
    if text is None:
        return None
    try:
        return json.loads(text, parse_float=Decimal, parse_int=Decimal)
    except ValueError as exc:
        raise JsonbError(f"invalid input syntax for type json: {exc}")


def _num(d):
    return str(d)


def render(v):
    if v is None:
        return None
    return _render(v)


def _render(v):
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {_render(x)}" for k, x in sorted(v.items())) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_render(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, Decimal):
        return _num(v)
    return json.dumps(v, ensure_ascii=False)
