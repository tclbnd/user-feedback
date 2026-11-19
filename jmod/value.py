"""jsonb input and output.

Input is JSON text; output is jsonb's text form.  Numbers are kept as
Decimal and printed the way PostgreSQL's numeric_out prints them (fixed
point, display scale preserved).  Object keys are unique and print in
jsonb's order: first by UTF-8 byte length, then bytewise.  JSON null is
represented by the JSON_NULL sentinel so that SQL NULL (None) stays
distinguishable from it.
"""
import json
from decimal import Decimal


class JsonbError(Exception):
    pass


class _JsonNull:
    """Singleton sentinel for JSON null (SQL NULL is plain None)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "null"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


JSON_NULL = _JsonNull()

# PostgreSQL numeric limits: up to 131072 digits before the decimal point
# and up to 16383 digits after it.
_MAX_WEIGHT = 131071          # max decimal exponent of the leading digit
_MAX_DSCALE = 16383           # max number of fractional digits


def parse(text):
    """Parse JSON text into a jsonb value; None input means SQL NULL."""
    if text is None:
        return None
    try:
        v = json.loads(text, parse_float=Decimal, parse_int=Decimal,
                       parse_constant=_reject_constant)
    except JsonbError:
        raise
    except ValueError:
        raise JsonbError("invalid input syntax for type json")
    except RecursionError:
        raise JsonbError("stack depth limit exceeded")
    try:
        return _convert(v)
    except RecursionError:
        raise JsonbError("stack depth limit exceeded")


def _reject_constant(s):
    # NaN, Infinity and -Infinity are not valid JSON
    raise JsonbError("invalid input syntax for type json")


def _convert(v):
    if v is None:
        return JSON_NULL
    if isinstance(v, bool):
        return v
    if isinstance(v, Decimal):
        return _check_numeric(v)
    if isinstance(v, str):
        return _check_string(v)
    if isinstance(v, list):
        return [_convert(x) for x in v]
    out = {}
    for k, x in v.items():
        out[_check_string(k)] = _convert(x)
    return out


def _check_string(s):
    for ch in s:
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise JsonbError("invalid Unicode escape")
    return s


def _check_numeric(d):
    t = d.as_tuple()
    if d != 0 and d.adjusted() > _MAX_WEIGHT:
        raise JsonbError("value overflows numeric format")
    if t.exponent < 0 and -t.exponent > _MAX_DSCALE:
        # numeric_in truncates the fraction to the maximum display scale
        drop = -t.exponent - _MAX_DSCALE
        digits = t.digits[:-drop] if drop < len(t.digits) else ()
        return Decimal((t.sign, digits, -_MAX_DSCALE))
    return d


def render(v):
    """Render a jsonb value as jsonb's text output; SQL NULL -> None."""
    if v is None:
        return None
    try:
        return _render(v)
    except RecursionError:
        raise JsonbError("stack depth limit exceeded")


def _render(v):
    if isinstance(v, dict):
        return "{" + ", ".join(
            json.dumps(k, ensure_ascii=False) + ": " + _render(x)
            for k, x in sorted(v.items(), key=lambda kv: _key_order(kv[0]))
        ) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_render(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is JSON_NULL or v is None:
        return "null"
    if isinstance(v, Decimal):
        return _num(v)
    return json.dumps(v, ensure_ascii=False)


def _key_order(k):
    # jsonb orders object keys by byte length, then bytewise
    b = k.encode("utf-8")
    return (len(b), b)


def _num(d):
    # numeric_out: always fixed point, display scale preserved
    t = d.as_tuple()
    digits = "".join(str(x) for x in t.digits) or "0"
    if t.exponent >= 0:
        body = digits + "0" * t.exponent
    else:
        frac = -t.exponent
        if len(digits) > frac:
            body = digits[:-frac] + "." + digits[-frac:]
        else:
            body = "0." + "0" * (frac - len(digits)) + digits
    return ("-" if t.sign else "") + body