"""Sequence objects, modelled on PostgreSQL's CREATE SEQUENCE.

Options: increment (default 1), minvalue, maxvalue, start, cycle (default
false), cache (default 1).

The stored state is ``last_value`` and ``is_called``: when ``is_called`` is
false the next value handed out is ``last_value`` itself, otherwise it is
``last_value + increment``.  As in PostgreSQL:

* an ascending sequence defaults to MINVALUE 1 / MAXVALUE 2^63-1 and START
  MINVALUE; a descending one defaults to MINVALUE -(2^63-1) / MAXVALUE -1
  and START MAXVALUE; an explicit MINVALUE/MAXVALUE must be inside
  [-(2^63-1), 2^63-1];
* MINVALUE must be less than MAXVALUE, CACHE must be positive and START must
  lie inside the bounds;
* nextval never returns a value outside [MINVALUE, MAXVALUE].  When the next
  value would overrun the bound the sequence wraps to the opposite bound
  (CYCLE) or raises an error without changing its state;
* a block of ``cache`` values is reserved from the shared state at once and
  handed out by the reserving session.  A block never crosses a bound: it is
  truncated at MINVALUE/MAXVALUE, and the following reservation either wraps
  (CYCLE) or fails.  Values reserved but not used are skipped for ever.

Sequence state is non-transactional, exactly as in PostgreSQL: nextval and
setval are never undone by a rollback.
"""

SEQ_MAXVALUE = 2**63 - 1    # PostgreSQL's SEQ_MAXVALUE (PG_INT64_MAX)
SEQ_MINVALUE = -SEQ_MAXVALUE  # PostgreSQL's SEQ_MINVALUE (-PG_INT64_MAX): the
                              # default MINVALUE of a descending sequence, and
                              # the lowest MINVALUE a sequence may be given


class SequenceError(Exception):
    pass


class Sequence:
    def __init__(self, name, increment=1, minvalue=None, maxvalue=None,
                 start=None, cycle=False, cache=1):
        increment = int(increment)
        if increment == 0:
            raise SequenceError("INCREMENT must not be zero")
        self._check_range("INCREMENT", increment)

        if minvalue is None:
            minvalue = 1 if increment > 0 else SEQ_MINVALUE
        if maxvalue is None:
            maxvalue = SEQ_MAXVALUE if increment > 0 else -1
        minvalue, maxvalue = int(minvalue), int(maxvalue)
        self._check_range("MINVALUE", minvalue)
        self._check_range("MAXVALUE", maxvalue)
        if minvalue >= maxvalue:
            raise SequenceError(
                f"MINVALUE ({minvalue}) must be less than MAXVALUE ({maxvalue})")

        cache = int(cache)
        if cache < 1:
            raise SequenceError(f"CACHE ({cache}) must be greater than zero")

        if start is None:
            start = minvalue if increment > 0 else maxvalue
        start = int(start)
        if not minvalue <= start <= maxvalue:
            raise SequenceError(
                f"START value ({start}) cannot be outside MINVALUE ({minvalue}) "
                f"and MAXVALUE ({maxvalue})")

        self.name = name
        self.increment = increment
        self.minvalue = minvalue
        self.maxvalue = maxvalue
        self.start = start
        self.cycle = bool(cycle)
        self.cache = cache
        self.last_value = start
        self.is_called = False

    @staticmethod
    def _check_range(option, value):
        if value < SEQ_MINVALUE or value > SEQ_MAXVALUE:
            raise SequenceError(
                f"{option} ({value}) is out of range "
                f"({SEQ_MINVALUE}..{SEQ_MAXVALUE})")

    def _overruns(self, value):
        return value > self.maxvalue if self.increment > 0 else value < self.minvalue

    def _raise_exhausted(self):
        if self.increment > 0:
            raise SequenceError(
                f'nextval: reached maximum value of sequence "{self.name}" '
                f"({self.maxvalue})")
        raise SequenceError(
            f'nextval: reached minimum value of sequence "{self.name}" '
            f"({self.minvalue})")

    def reserve(self):
        """Reserve the next block of values from the shared state.

        Returns the list of values the reserving session may hand out.
        """
        first, count = self.reserve_block()
        return [first + i * self.increment for i in range(count)]

    def reserve_block(self):
        """Reserve the next block, returned as ``(first_value, count)``.

        The shared state moves to the last value of the block, so concurrent
        sessions never get overlapping values.  A block never crosses a bound:
        it is truncated at MINVALUE/MAXVALUE (hence ``count`` may be smaller
        than ``cache``), and the following reservation either wraps to the
        opposite bound (CYCLE) or raises an error.
        """
        step = self.increment
        if self.is_called:
            first = self.last_value + step
            if self._overruns(first):
                if not self.cycle:
                    self._raise_exhausted()
                first = self.minvalue if step > 0 else self.maxvalue
        else:
            first = self.last_value

        if step > 0:
            room = (self.maxvalue - first) // step + 1
        else:
            room = (first - self.minvalue) // (-step) + 1
        count = min(self.cache, room)

        self.last_value = first + (count - 1) * step
        self.is_called = True
        return first, count

    def setval(self, value, is_called=True):
        value = int(value)
        if not self.minvalue <= value <= self.maxvalue:
            raise SequenceError(
                f'setval: value {value} is out of bounds for sequence '
                f'"{self.name}" ({self.minvalue}..{self.maxvalue})')
        self.last_value = value
        self.is_called = bool(is_called)
        return value
