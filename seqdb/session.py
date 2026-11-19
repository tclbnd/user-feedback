"""Client sessions: nextval, currval, lastval, setval, and transactions.

Every session (application server) has its own view of the sequences, as
each PostgreSQL backend does:

* a block of ``cache`` values is reserved from the shared sequence at once
  and handed out locally, so concurrent sessions reserve the following blocks
  and their values interleave without ever being duplicated.  A ``setval``
  performed by another session becomes visible here only once this session's
  own block is used up;
* ``currval`` reports the value this session last obtained for the sequence;
  another session advancing the sequence does not change it.  ``nextval`` and
  ``setval`` both establish that value, so after ``setval(s, n, false)``
  currval answers ``n`` and the next nextval also returns ``n``.  A sequence
  this session never touched has no currval;
* ``lastval`` reports the current value of the sequence ``nextval`` was most
  recently applied to in this session; it errors if this session has not
  called ``nextval`` yet (a bare ``setval`` does not select a sequence);
* ``setval`` writes the shared sequence and drops the calling session's own
  cached block, so the next nextval sees the new value.  It cannot reach
  blocks already reserved by other sessions; those keep handing out their
  cached values;
* sequence changes are non-transactional: COMMIT and ROLLBACK neither undo a
  nextval nor a setval, and a rollback keeps the values cached by the session
  (values already obtained are never handed out twice).
"""
from .sequence import SequenceError


class Session:
    def __init__(self, name, catalog):
        self.name = name
        self.catalog = catalog
        # sequence name -> [next value of the reserved block, values left in it]
        self.cached = {}
        # sequence name -> value currval reports (set by nextval and setval)
        self.currvals = {}
        # sequence nextval was last applied to in this session (None: never)
        self.last_sequence = None
        self.in_txn = False

    def nextval(self, seq_name):
        seq = self.catalog.sequence(seq_name)
        block = self.cached.get(seq_name)
        if block is None:
            first, count = seq.reserve_block()
            block = self.cached[seq_name] = [first, count]
        value = block[0]
        block[0] += seq.increment
        block[1] -= 1
        if block[1] == 0:
            del self.cached[seq_name]
        self.currvals[seq_name] = value
        self.last_sequence = seq_name
        return value

    def currval(self, seq_name):
        self.catalog.sequence(seq_name)
        if seq_name not in self.currvals:
            raise SequenceError(
                f'currval of sequence "{seq_name}" is not yet defined in this session')
        return self.currvals[seq_name]

    def lastval(self):
        if self.last_sequence is None:
            raise SequenceError("lastval is not yet defined in this session")
        return self.currval(self.last_sequence)

    def setval(self, seq_name, value, is_called=True):
        seq = self.catalog.sequence(seq_name)
        value = seq.setval(value, is_called)
        # The caller's cached block is stale and is dropped; blocks already
        # reserved by other sessions stay valid.  The next nextval returns
        # ``value`` itself when is_called is false, else ``value + increment``.
        self.cached.pop(seq_name, None)
        self.currvals[seq_name] = value
        return value

    # transactions ------------------------------------------------------------
    # Sequence operations are non-transactional.  These only track the state,
    # so a rollback neither restores the shared sequence nor reclaims the
    # block cached by this session: values allocated before it are not reused.
    def begin(self):
        self.in_txn = True

    def commit(self):
        self.in_txn = False

    def rollback(self):
        self.in_txn = False
