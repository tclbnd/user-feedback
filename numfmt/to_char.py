"""to_char(number, format) of the report engine (PostgreSQL template patterns).

The value is an integer or a numeric literal given as text ("148.5");
NULL in either argument gives NULL.
"""
from decimal import ROUND_HALF_UP, Decimal


class FormatError(Exception):
    pass


DIGITS = "90"


def to_char(value, fmt):
    if value is None or fmt is None:
        return None
    x = Decimal(str(value))
    int_fmt, _, frac_fmt = fmt.partition(".")
    decimals = sum(1 for ch in frac_fmt if ch in DIGITS)
    q = x.quantize(Decimal(1).scaleb(-decimals), ROUND_HALF_UP)
    text = f"{abs(q):.{decimals}f}"
    int_digits, _, frac_digits = text.partition(".")
    if int_digits == "0":
        int_digits = ""

    out = []
    pending = list(int_digits)
    for ch in reversed(int_fmt):
        if ch in DIGITS:
            if pending:
                out.append(pending.pop())
            else:
                out.append("0" if ch == "0" else " ")
        elif ch == ",":
            out.append("," if pending else " ")
        else:
            out.append(ch)
    result = "".join(reversed(out))
    if "." in fmt:
        result += "." + frac_digits
    if q < 0:
        result = "-" + result.lstrip()
    return result
