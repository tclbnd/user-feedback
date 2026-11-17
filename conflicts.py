"""Read registrations, rw-antidependency edges and SSI dangerous structures.

A rw-antidependency edge ``R -> W`` records that ``R`` read a key (or a
range covering it) while a concurrent ``W`` wrote, inserted or deleted that
key.  A transaction carrying both an incoming and an outgoing edge is a
pivot: the pattern ``T1 -> T2 -> T3`` (``T1`` may equal ``T3``) becomes a
dangerous structure once ``T3`` has committed before ``T2`` and ``T1``.
A read-only ``T1`` only takes part when ``T3`` committed before ``T1``
began; otherwise its snapshot is provably safe.
"""


class ConflictTracker:
    def __init__(self):
        self.points = {}          # txn name -> {keys registered by get}
        self.ranges = {}          # txn name -> [(lo, hi) ranges registered by scan]
        self.edges = set()        # {(reader_name, writer_name)} rw-antidependencies

    # -- read registration ------------------------------------------------
    def register(self, tx, lo, hi=None):
        """Register a point read (get) or a whole range read (scan).

        Range registration covers keys that do not exist yet, so a later
        insert inside the range still produces an antidependency edge.
        """
        if hi is None:
            self.points.setdefault(tx.name, set()).add(lo)
        else:
            self.ranges.setdefault(tx.name, []).append((lo, hi))

    def readers_of(self, key):
        """Names of every transaction whose registrations cover ``key``."""
        names = {n for n, keys in self.points.items() if key in keys}
        names |= {n for n, rs in self.ranges.items()
                  if any(lo <= key <= hi for lo, hi in rs)}
        return names

    # -- rw-antidependency edges ------------------------------------------
    def add_edge(self, reader, writer):
        """Record reader -> writer. Returns True when the edge is new."""
        if reader is writer or reader.status == "aborted" or writer.status == "aborted":
            return False
        pair = (reader.name, writer.name)
        if pair in self.edges:
            return False
        self.edges.add(pair)
        return True

    def purge(self, name):
        """Drop every registration and edge belonging to one transaction."""
        self.points.pop(name, None)
        self.ranges.pop(name, None)
        self.edges = {pair for pair in self.edges if name not in pair}

    # -- dangerous structure detection ------------------------------------
    def dangerous_structures(self, txns):
        """Yield ``(T1, T2, T3)`` name triples dangerous right now.

        ``T1 -> T2 -> T3`` requires a pivot ``T2`` (incoming and outgoing
        edge), ``T3`` already committed before ``T2`` and ``T1`` (the
        self comparison is vacuous when ``T1 is T3``), and the read-only
        safe-snapshot rule for ``T1``.
        """
        outgoing, incoming = {}, {}
        for reader, writer in self.edges:
            r, w = txns[reader], txns[writer]
            if r.status == "aborted" or w.status == "aborted":
                continue
            outgoing.setdefault(reader, set()).add(writer)
            incoming.setdefault(writer, set()).add(reader)

        for t2 in outgoing.keys() & incoming.keys():
            t2_txn = txns[t2]
            for t1 in incoming[t2]:
                t1_txn = txns[t1]
                for t3 in outgoing[t2]:
                    t3_txn = txns[t3]
                    if t3_txn.status != "committed":
                        continue
                    # T3 must have committed before the pivot ...
                    if not t3_txn.commit_seq < t2_txn.commit_seq:
                        continue
                    # ... and before T1 (vacuous when T1 is T3, a 2-cycle).
                    if t1 != t3 and not t3_txn.commit_seq < t1_txn.commit_seq:
                        continue
                    # A read-only T1 has a safe snapshot unless T3
                    # committed before T1 even began.
                    if t1_txn.read_only and not t3_txn.commit_seq < t1_txn.start_seq:
                        continue
                    yield (t1, t2, t3)
