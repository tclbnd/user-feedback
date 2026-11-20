"""Aggregate functions of the dashboard engine (PostgreSQL names and behaviour).

Every aggregate receives the input type of the column (int4, int8, numeric,
text, bool) and the list of input values in scan order; NULL is None.
Numeric values arrive as text ("1.50").  Results are returned as text, the
way psql prints them, or None for NULL.

PostgreSQL semantics reproduced here:

* all aggregates ignore NULL inputs, except count(*), array_agg and
  json_agg; every aggregate except count returns NULL on an empty group;
* count returns bigint, sum(int4) returns int8 (and overflows like
  int8inc), sum(int8)/avg return numeric;
* numeric results carry PostgreSQL's display scale: sum/avg keep the input
  scale, division uses select_div_scale() and square root uses sqrt_var()'s
  scale rule from src/backend/utils/adt/numeric.c, rounding is half away
  from zero;
* percentile_cont returns float8, printed the way float8out prints with
  extra_float_digits = 1 (shortest round-trip text);
* array_agg/json_agg emit exactly the text array_out/escape_json emit.
"""
import json
import math
from decimal import Decimal
from fractions import Fraction


class AggregateError(Exception):
    pass


INT8_MIN = -(2 ** 63)
INT8_MAX = 2 ** 63 - 1

# Limits from numeric.c
DEC_DIGITS = 4                    # decimal digits per base-NBASE digit
NUMERIC_MIN_SIG_DIGITS = 16
NUMERIC_MIN_DISPLAY_SCALE = 0
NUMERIC_MAX_DISPLAY_SCALE = 1004  # NUMERIC_MAX_PRECISION (1000) + 4


# --------------------------------------------------------------------------
# Exact numeric with display scale (PostgreSQL "numeric")
# --------------------------------------------------------------------------

def _e10(fr):
    """floor(log10(|fr|)) of a nonzero Fraction, computed exactly."""
    p = abs(fr.numerator)
    q = fr.denominator
    e = len(str(p)) - len(str(q))
    if e >= 0:
        if p < q * 10 ** e:
            e -= 1
    elif p * 10 ** (-e) < q:
        e -= 1
    return e


def _first_digit(fr, weight):
    """First base-10000 digit of |fr|, given its base-10000 weight."""
    p = abs(fr.numerator)
    q = fr.denominator
    k = DEC_DIGITS * weight
    if k >= 0:
        return p // (q * 10 ** k)
    return (p * 10 ** (-k)) // q


def _round_away(fr, rscale):
    """Round fr to rscale fractional decimal digits, half away from zero."""
    scaled = fr * Fraction(10) ** rscale
    a = abs(scaled)
    fl = a.numerator // a.denominator
    if a - fl >= Fraction(1, 2):
        fl += 1
    if scaled < 0:
        fl = -fl
    return Fraction(fl) / Fraction(10) ** rscale


class _Num:
    """Exact numeric value plus PostgreSQL display scale (dscale).

    v is the exact value as a Fraction (always an integer multiple of
    10**-ds); ds is the number of fractional digits numeric_out prints.
    """
    __slots__ = ("v", "ds")

    def __init__(self, v, ds):
        self.v = v
        self.ds = ds

    @classmethod
    def of_int(cls, i):
        return cls(Fraction(i), 0)

    @classmethod
    def of_text(cls, s):
        d = Decimal(s)
        return cls(Fraction(d), max(0, -d.as_tuple().exponent))

    def add(self, other):
        return _Num(self.v + other.v, max(self.ds, other.ds))

    def neg(self):
        return _Num(-self.v, self.ds)

    def mul(self, other):
        return _Num(self.v * other.v, self.ds + other.ds)

    def div(self, other):
        rscale = _select_div_scale(self, other)
        return _Num(_round_away(self.v / other.v, rscale), rscale)

    def sqrt(self):
        # sqrt_var(): sqrt(0) is 0 with NUMERIC_MIN_SIG_DIGITS digits
        if self.v == 0:
            return _Num(Fraction(0), NUMERIC_MIN_SIG_DIGITS)
        weight = _e10(self.v) // DEC_DIGITS
        rscale = NUMERIC_MIN_SIG_DIGITS - weight * DEC_DIGITS // 2
        rscale = max(rscale, self.ds, NUMERIC_MIN_DISPLAY_SCALE)
        rscale = min(rscale, NUMERIC_MAX_DISPLAY_SCALE)
        # round(sqrt(v), rscale), half away from zero, computed exactly:
        # sqrt(v) * 10^rscale = sqrt(v * 10^(2*rscale)), an integer radicand
        m = self.v * Fraction(10) ** (2 * rscale)
        assert m.denominator == 1
        m = m.numerator
        s = math.isqrt(m)
        if m > s * (s + 1):  # fractional part of sqrt(m) exceeds 1/2
            s += 1
        return _Num(Fraction(s, 10 ** rscale), rscale)

    def show(self):
        # numeric_out: exactly ds fractional digits, no negative zero
        neg = self.v < 0
        k = (abs(self.v) * 10 ** self.ds).numerator
        s = str(k)
        if self.ds:
            s = s.rjust(self.ds + 1, "0")
            s = s[:-self.ds] + "." + s[-self.ds:]
        return "-" + s if neg else s


def _select_div_scale(a, b):
    """Display scale of a/b, as select_div_scale() in numeric.c."""
    if a.v == 0:
        weight1 = first1 = 0
    else:
        weight1 = _e10(a.v) // DEC_DIGITS
        first1 = _first_digit(a.v, weight1)
    weight2 = _e10(b.v) // DEC_DIGITS
    first2 = _first_digit(b.v, weight2)
    qweight = weight1 - weight2
    if first1 <= first2:
        qweight -= 1
    rscale = NUMERIC_MIN_SIG_DIGITS - qweight * DEC_DIGITS
    rscale = max(rscale, a.ds, b.ds, NUMERIC_MIN_DISPLAY_SCALE)
    return min(rscale, NUMERIC_MAX_DISPLAY_SCALE)


def _variance(nums):
    """Sample variance of _Num values: (N*S(x^2) - S(x)^2) / (N*(N-1))."""
    n = len(nums)
    s1 = s2 = None
    for x in nums:
        s1 = x if s1 is None else s1.add(x)
        xx = x.mul(x)
        s2 = xx if s2 is None else s2.add(xx)
    num = _Num.of_int(n).mul(s2).add(s1.mul(s1).neg())
    return num.div(_Num.of_int(n * (n - 1)))


# --------------------------------------------------------------------------
# Output renderers
# --------------------------------------------------------------------------

def _show(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return "t" if v else "f"
    return str(v)


def _float8_out(x):
    """float8out with extra_float_digits = 1 (shortest round-trip text)."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "-0" if math.copysign(1.0, x) < 0 else "0"
    mant, _, exp = repr(abs(x)).lower().partition("e")
    ip, _, fp = mant.partition(".")
    digits = ip + fp
    point = len(ip) + (int(exp) if exp else 0)
    stripped = digits.lstrip("0")
    point -= len(digits) - len(stripped)
    digits = stripped.rstrip("0")
    e10 = point - 1
    if -4 <= e10 <= 14:
        if e10 >= len(digits) - 1:
            out = digits + "0" * (e10 - len(digits) + 1)
        elif e10 >= 0:
            out = digits[:e10 + 1] + "." + digits[e10 + 1:]
        else:
            out = "0." + "0" * (-e10 - 1) + digits
    else:
        out = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
        out += "e%+03d" % e10
    return ("-" if x < 0 else "") + out


_ARRAY_QUOTE_CHARS = set('{},"\\ \t\n\v\f\r')


def _array_elem(typ, v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        txt = "t" if v else "f"
    elif typ == "numeric":
        txt = _Num.of_text(v).show()
    else:
        txt = str(v)
    # array_out: quote empty strings, "NULL" (any case), and elements
    # containing braces, comma, quote, backslash or whitespace
    if txt == "" or txt.lower() == "null" or \
            any(c in _ARRAY_QUOTE_CHARS for c in txt):
        return '"' + txt.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return txt


def _json_elem(typ, v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if typ == "numeric":
        return _Num.of_text(v).show()
    if typ == "text":
        return json.dumps(v, ensure_ascii=False)
    return str(v)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _nonnull(values):
    return [v for v in values if v is not None]


def _typed_nums(typ, values):
    if typ == "numeric":
        return [_Num.of_text(v) for v in values]
    return [_Num.of_int(v) for v in values]


def _check_fraction(fraction):
    f = float(fraction)
    if math.isnan(f) or f < 0 or f > 1:
        raise AggregateError(
            "percentile value %g is not between 0 and 1" % f)
    return f


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------

def count(typ, values):
    return str(len(_nonnull(values)))


def count_star(typ, values):
    return str(len(values))


def count_distinct(typ, values):
    seen = set()
    for v in values:
        if v is None:
            continue
        # numeric compares by value, not text: 1.0 and 1.00 are one value
        seen.add(Fraction(Decimal(v)) if typ == "numeric" else v)
    return str(len(seen))


def sum_(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    if typ == "int4":
        total = 0  # sum(int4) returns int8 and overflows like int8inc()
        for v in vals:
            total += v
            if not INT8_MIN <= total <= INT8_MAX:
                raise AggregateError("bigint out of range")
        return str(total)
    if typ == "int8":
        return str(sum(vals))  # sum(int8) returns numeric
    acc = None
    for n in _typed_nums(typ, vals):
        acc = n if acc is None else acc.add(n)
    return acc.show()


def avg(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    acc = None
    for n in _typed_nums(typ, vals):
        acc = n if acc is None else acc.add(n)
    return acc.div(_Num.of_int(len(vals))).show()


def min_(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    if typ == "numeric":
        best = None
        for n in _typed_nums(typ, vals):
            if best is None or n.v < best.v:
                best = n
        return best.show()
    return _show(min(vals))


def max_(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    if typ == "numeric":
        best = None
        for n in _typed_nums(typ, vals):
            if best is None or n.v > best.v:
                best = n
        return best.show()
    return _show(max(vals))


def string_agg(typ, values, delimiter):
    vals = [str(v) for v in values if v is not None]
    if not vals:
        return None
    return delimiter.join(vals)


def array_agg(typ, values):
    if not values:
        return None
    return "{" + ",".join(_array_elem(typ, v) for v in values) + "}"


def json_agg(typ, values):
    if not values:
        return None
    return "[" + ", ".join(_json_elem(typ, v) for v in values) + "]"


def bool_and(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    return _show(all(vals))


def bool_or(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    return _show(any(vals))


def var_samp(typ, values):
    nums = _typed_nums(typ, _nonnull(values))
    if len(nums) < 2:
        return None
    return _variance(nums).show()


def stddev_samp(typ, values):
    nums = _typed_nums(typ, _nonnull(values))
    if len(nums) < 2:
        return None
    return _variance(nums).sqrt().show()


def percentile_cont(typ, values, fraction):
    p = _check_fraction(fraction)
    vals = sorted(float(v) for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return None
    # continuous percentile: interpolate in float8, result is float8
    rownum = 1.0 + p * (n - 1)
    firstrow = math.floor(rownum)
    secondrow = math.ceil(rownum)
    val = vals[firstrow - 1] + (rownum - firstrow) * \
        (vals[secondrow - 1] - vals[firstrow - 1])
    return _float8_out(val)


def percentile_disc(typ, values, fraction):
    p = _check_fraction(fraction)
    vals = _nonnull(values)
    n = len(vals)
    if n == 0:
        return None
    if typ == "numeric":
        vals = sorted(_typed_nums(typ, vals), key=lambda x: x.v)
    else:
        vals = sorted(vals)
    rownum = 1 if p == 0 else math.ceil(p * n)
    rownum = max(1, min(rownum, n))
    pick = vals[rownum - 1]
    if isinstance(pick, _Num):
        return pick.show()
    return _show(pick)


def mode(typ, values):
    vals = _nonnull(values)
    if not vals:
        return None
    if typ == "numeric":
        items = sorted(_typed_nums(typ, vals), key=lambda x: x.v)
        keys = [x.v for x in items]
    else:
        items = sorted(vals)
        keys = items
    # most frequent value; ties go to the first run in sort order
    best = best_run = None
    cur = 1
    for i in range(1, len(items) + 1):
        if i < len(items) and keys[i] == keys[i - 1]:
            cur += 1
            continue
        if best_run is None or cur > best_run:
            best_run, best = cur, items[i - 1]
        cur = 1
    if isinstance(best, _Num):
        return best.show()
    return _show(best)


AGGREGATES = {
    "count": count, "count_star": count_star, "count_distinct": count_distinct, "sum": sum_, "avg": avg,
    "min": min_, "max": max_, "string_agg": string_agg, "array_agg": array_agg, "json_agg": json_agg,
    "bool_and": bool_and, "bool_or": bool_or, "every": bool_and, "stddev_samp": stddev_samp,
    "var_samp": var_samp, "percentile_cont": percentile_cont, "percentile_disc": percentile_disc, "mode": mode,
}
