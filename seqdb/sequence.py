"""Cached sequences with crash- and failover-safe block reservation.

Taking a number never touches disk: a sequence reserves a whole block of
``cache`` numbers up front.  The reservation is handed to the ``persist``
callback (which writes a ``seqreserve`` WAL record and fsyncs it) *before*
any number from the new block is issued.  Because the reservation lives in
the WAL it is:

* durable across a crash -- the fsync happens before the first caller sees
  a number from the block, and
* replicated to the standby -- log shipping carries the same record that
  carries table rows, so a promoted standby knows the high-water mark.

On recovery a sequence jumps to the end of its last reserved block.  The
unspent tail of a partially used block becomes a gap; gaps are allowed,
reissuing a number is not.

Sequence advancement is deliberately non-transactional: ``nextval`` never
goes backwards, so a transaction rollback does not return a number.
"""


class Sequence:
    def __init__(self, name, cache=20, start=1, persist=None):
        self.name = name
        self.cache = cache
        self.start = start
        # persist(name, reserved) must make the reservation durable and
        # replicated before returning. Tests can pass a recording stub.
        self.persist = persist or (lambda _name, _reserved: None)
        self.reserved = start - 1
        self.current = start - 1
        self.ops = 0

    def nextval(self):
        if self.current >= self.reserved:
            new_reserved = self.current + self.cache
            # Reserve and fsync the whole block first; only then may a
            # value out of it be handed to a caller.
            self.persist(self.name, new_reserved)
            self.reserved = new_reserved
        self.current += 1
        self.ops += 1
        return self.current

    def replay_reserve(self, reserved):
        """Apply a ``seqreserve`` WAL record during log replay/recovery.

        The sequence high-water mark jumps to the reserved block end, so
        every number already issued (all <= reserved) stays out of reach.
        Replaying the same record twice is harmless.
        """
        if reserved > self.reserved:
            self.reserved = reserved
        self.current = self.reserved

    def snapshot(self):
        return self.current
