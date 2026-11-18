"""Logical dump of a set of tables.

The dump is produced in chunks so it can run next to the live workload: a
generator yields after every chunk and the server runs other transactions
in between.

A dump is usable for recovery only if every table reflects the same point
in time.  The dump therefore pins one MVCC snapshot before it reads
anything, and reads every chunk at that timestamp.  Transactions that
commit during the dump create versions the snapshot does not see, so
orders and their lines always come from the same state.  Holding the
snapshot also tells autovacuum to keep the versions it still needs, so
the dump never has to lock the tables and writers never wait for it.
"""

CHUNK = 10


class Dump:
    def __init__(self, store, tables, chunk=CHUNK, lock_tables=False):
        self.store = store
        self.tables = tables
        self.chunk = chunk
        self.lock_tables = lock_tables
        self.data = {t: {} for t in tables}
        self.snapshot = None
        self._runs = 0

    def run(self):
        # One holder name per run: a snapshot must be released exactly once.
        holder = ("dump", id(self), self._runs)
        self._runs += 1
        registered = False
        try:
            if self.lock_tables:
                self.store.locked = True
            # Pin the read point before touching any table.
            self.snapshot = self.store.now()
            self.store.register_snapshot(holder, self.snapshot)
            registered = True
            for table in self.tables:
                # The key set is fixed by the snapshot, so slicing it is a
                # stable pagination even while rows are inserted or deleted.
                keys = self.store.keys(table, self.snapshot)
                for start in range(0, len(keys), self.chunk):
                    for key in keys[start:start + self.chunk]:
                        self.data[table][key] = self.store.read(table, key, self.snapshot)
                    yield
        finally:
            if registered:
                self.store.release_snapshot(holder)
            if self.lock_tables:
                self.store.locked = False
