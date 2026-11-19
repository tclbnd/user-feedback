"""Client sessions: nextval, currval, lastval, setval, and transactions."""
from .sequence import SequenceError


class Session:
    def __init__(self, name, catalog):
        self.name = name
        self.catalog = catalog
        self.cached = {}        # sequence name -> [values reserved by this session]
        self.cache_epoch = {}
        self.in_txn = False
        self.undo = []

    def nextval(self, seq_name):
        seq = self.catalog.sequence(seq_name)
        if self.cache_epoch.get(seq_name) != seq.epoch:
            self.cached[seq_name] = []
            self.cache_epoch[seq_name] = seq.epoch
        if not self.cached.get(seq_name):
            if self.in_txn:
                self.undo.append((seq, seq.last_value, seq.is_called))
            self.cached[seq_name] = seq.reserve()
        value = self.cached[seq_name].pop(0)
        self.catalog.last_values[seq_name] = value
        self.catalog.last_sequence = seq_name
        return value

    def currval(self, seq_name):
        self.catalog.sequence(seq_name)
        if seq_name not in self.catalog.last_values:
            raise SequenceError(f'currval of sequence "{seq_name}" is not yet defined in this session')
        return self.catalog.last_values[seq_name]

    def lastval(self):
        if self.catalog.last_sequence is None:
            raise SequenceError("lastval is not yet defined in this session")
        return self.catalog.last_values[self.catalog.last_sequence]

    def setval(self, seq_name, value, is_called=True):
        seq = self.catalog.sequence(seq_name)
        if self.in_txn:
            self.undo.append((seq, seq.last_value, seq.is_called))
        return seq.setval(value if is_called else value - seq.increment, True)

    # transactions ------------------------------------------------------------
    def begin(self):
        self.in_txn, self.undo = True, []

    def commit(self):
        self.in_txn, self.undo = False, []

    def rollback(self):
        for seq, last, called in reversed(self.undo):
            seq.last_value, seq.is_called = last, called
        self.in_txn, self.undo = False, []
