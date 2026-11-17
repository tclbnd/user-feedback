"""Snapshot isolation engine with serializable conflict checking."""
from conflicts import ConflictTracker

INF = float("inf")


class SerializationFailure(Exception):
    pass


class Transaction:
    def __init__(self, name, snapshot, start_seq, read_only):
        self.name, self.snapshot, self.start_seq, self.read_only = name, snapshot, start_seq, read_only
        self.status, self.commit_seq, self.writes, self.doomed = "running", INF, {}, False

    def concurrent_with(self, other):
        return self.start_seq < other.commit_seq and other.start_seq < self.commit_seq


class Engine:
    def __init__(self, initial=None):
        self.versions, self.clock, self.seq = {}, 1, 0
        for key, value in (initial or {}).items():
            self.versions[int(key)] = [(1, value, None)]
        self.txns, self.tracker, self.commit_order = {}, ConflictTracker(), []

    def begin(self, name, read_only=False):
        self.seq += 1
        self.txns[name] = Transaction(name, self.clock, self.seq, read_only)
        return self.txns[name]

    def _check(self, tx):
        if tx.doomed or tx.status == "aborted":
            self.abort(tx)
            raise SerializationFailure(tx.name)

    def _edge(self, reader, writer):
        if not self.tracker.add_edge(reader, writer):
            return
        # Structures are checked whenever an edge is recorded.
        self._detect()

    def _victim(self, t1, t2):
        """Victim priority: the pivot T2 while it has not committed, else T1."""
        candidate = t2 if t2.status != "committed" else t1
        if candidate.status != "committed":
            candidate.doomed = True

    def _detect(self):
        for t1, t2, t3 in self.tracker.dangerous_structures(self.txns):
            self._victim(self.txns[t1], self.txns[t2])

    def _gc(self):
        """Forget finished transactions once they can prove nothing new.

        Registrations and edges stay in force while a concurrent
        transaction can still write. A finished transaction is therefore
        retained while either it, or any transaction linked to it by an
        antidependency edge, is still concurrent with a running one.
        """
        running = [o for o in self.txns.values() if o.status == "running"]
        if not running:
            for name in list(self.txns):
                self.tracker.purge(name)
            return
        for tx in list(self.txns.values()):
            if tx.status == "running":
                continue
            if any(tx.concurrent_with(o) for o in running):
                continue
            linked = {a for a, b in self.tracker.edges if b == tx.name}
            linked |= {b for a, b in self.tracker.edges if a == tx.name}
            if any(self.txns[y].status != "aborted"
                   and any(self.txns[y].concurrent_with(r) for r in running)
                   for y in linked):
                continue
            self.tracker.purge(tx.name)

    def _hidden_writers(self, tx, key):
        names = [w for ts, _, w in self.versions.get(key, []) if ts > tx.snapshot and w]
        names += [o.name for o in self.txns.values() if o is not tx and o.status == "running" and key in o.writes]
        return [self.txns[n] for n in names]

    def _value(self, tx, key):
        if key in tx.writes:
            return tx.writes[key]
        seen = [v for ts, v, _ in self.versions.get(key, []) if ts <= tx.snapshot]
        return seen[-1] if seen else None

    def read(self, tx, lo, hi=None):
        self._check(tx)
        if hi is None:
            keys = {lo}
        else:
            # The whole range is registered, including absent keys, so
            # phantom inserts are detected as predicate conflicts.
            self.tracker.register(tx, lo, hi)
            keys = {k for k in self.versions if lo <= k <= hi}
            keys |= {k for o in self.txns.values()
                     if o is not tx and o.status == "running"
                     for k in o.writes if lo <= k <= hi}
        for key in sorted(keys):
            if hi is None:
                self.tracker.register(tx, key)
            for writer in self._hidden_writers(tx, key):
                if writer.concurrent_with(tx):
                    self._edge(tx, writer)
        self._check(tx)
        keys |= {k for k in tx.writes if hi is not None and lo <= k <= hi}
        rows = [[k, self._value(tx, k)] for k in sorted(keys)]
        return rows[0][1] if hi is None else [r for r in rows if r[1] is not None]

    def write(self, tx, key, value):
        self._check(tx)
        if self._hidden_writers(tx, key):
            self.abort(tx)
            raise SerializationFailure(tx.name)
        for name in self.tracker.readers_of(key):
            reader = self.txns[name]
            if reader is not tx and reader.concurrent_with(tx):
                self._edge(reader, tx)
        tx.writes[key] = value
        self._check(tx)

    def commit(self, tx):
        self._check(tx)
        if tx.writes:
            self.clock += 1
            for key, value in tx.writes.items():
                self.versions.setdefault(key, []).append((self.clock, value, tx.name))
        self.seq += 1
        tx.status, tx.commit_seq = "committed", self.seq
        self.commit_order.append(tx.name)
        # Structures are re-checked whenever a transaction commits: a
        # newly visible commit order can make a pivot dangerous.
        self._detect()
        self._gc()
        self._check(tx)

    def abort(self, tx):
        if tx.status == "running":
            tx.status, tx.writes, tx.doomed = "aborted", {}, False
            self._gc()

    def outcome(self):
        state = {str(k): vs[-1][1] for k, vs in sorted(self.versions.items()) if vs[-1][1] is not None}
        return {"committed": self.commit_order,
                "aborted": [n for n, t in self.txns.items() if t.status == "aborted"], "state": state}
