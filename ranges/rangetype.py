"""Range values of the booking engine (PostgreSQL int4range / numrange / daterange).

A range is written like PostgreSQL range literals: '[1,5)', '(,10]',
'empty'.  Elements are ints, Decimals or dates depending on the type.
"""
import datetime
from decimal import Decimal


class RangeError(Exception):
    pass


SUBTYPES = {
    "int4range": int,
    "numrange": Decimal,
    "daterange": datetime.date.fromisoformat,
}


class Range:
    def __init__(self, typ, lower, upper, lower_inc=True, upper_inc=False, empty=False):
        self.typ, self.lower, self.upper = typ, lower, upper
        self.lower_inc, self.upper_inc, self.empty = lower_inc, upper_inc, empty

    @classmethod
    def parse(cls, typ, text):
        text = text.strip()
        if text == "empty":
            return cls(typ, None, None, empty=True)
        conv = SUBTYPES[typ]
        lo, _, hi = text[1:-1].partition(",")
        lower = conv(lo.strip()) if lo.strip() else None
        upper = conv(hi.strip()) if hi.strip() else None
        return cls(typ, lower, upper, text[0] == "[", text[-1] == "]")

    def __str__(self):
        if self.empty:
            return "empty"
        lo = "" if self.lower is None else str(self.lower)
        hi = "" if self.upper is None else str(self.upper)
        return ("[" if self.lower_inc else "(") + lo + "," + hi + ("]" if self.upper_inc else ")")
