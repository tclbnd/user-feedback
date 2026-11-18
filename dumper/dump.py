"""Logical dump of a set of tables.

The dump is produced in chunks so it can run next to the live workload: a
generator yields after every chunk and the server runs other transactions
in between.
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

    def run(self):
        if self.lock_tables:
            self.store.locked = True
        for table in self.tables:
            offset = 0
            while True:
                ts = self.store.now()
                keys = self.store.keys(table, ts)[offset:offset + self.chunk]
                if not keys:
                    break
                for key in keys:
                    self.data[table][key] = self.store.read(table, key, ts)
                offset += len(keys)
                yield
        self.store.locked = False
