"""Sequence objects, modelled on PostgreSQL's CREATE SEQUENCE.

Options: increment (default 1), minvalue, maxvalue, start, cycle (default
false), cache (default 1).  The stored state is ``last_value`` and
``is_called``: when ``is_called`` is false the next value handed out is
``last_value`` itself, otherwise ``last_value + increment``.

With ``cache`` > 1 a session that needs a value reserves ``cache`` values
at once and hands them out locally; other sessions reserve the following
block.
"""

BIGINT_MAX = 2**63 - 1
BIGINT_MIN = -2**63


class SequenceError(Exception):
    pass


class Sequence:
    def __init__(self, name, increment=1, minvalue=None, maxvalue=None, start=None, cycle=False, cache=1):
        if increment == 0:
            raise SequenceError("INCREMENT must not be zero")
        self.name = name
        self.increment = increment
        self.minvalue = 1 if minvalue is None else minvalue
        self.maxvalue = BIGINT_MAX if maxvalue is None else maxvalue
        self.start = (self.minvalue if increment > 0 else self.maxvalue) if start is None else start
        if not self.minvalue <= self.start <= self.maxvalue:
            raise SequenceError(f"START value ({self.start}) cannot be outside MINVALUE/MAXVALUE")
        self.cycle = cycle
        self.cache = cache
        self.last_value = self.start
        self.is_called = False
        self.epoch = 0          # bumped by setval

    def _advance(self, value):
        nxt = value + self.increment
        if nxt > self.maxvalue or nxt < self.minvalue:
            if not self.cycle:
                return nxt
            return self.start
        return nxt

    def reserve(self):
        """Take the next block of ``cache`` values from the shared state."""
        values = []
        value = self.last_value if not self.is_called else self._advance(self.last_value)
        for _ in range(self.cache):
            values.append(value)
            self.last_value, self.is_called = value, True
            value = self._advance(value)
        return values

    def setval(self, value, is_called=True):
        self.last_value = value
        self.is_called = is_called
        self.epoch += 1
        return value
