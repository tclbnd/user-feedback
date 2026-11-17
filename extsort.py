"""Sort operator."""
from keys import row_key


class MemoryExceeded(Exception):
    pass


class SortOperator:
    def __init__(self, keys, work_mem=10000, fan_in=8, limit=None, tmpdir=None):
        if not 2 <= fan_in <= work_mem:
            raise ValueError("fan-in must be between 2 and work_mem")
        self.keys = keys
        self.work_mem, self.fan_in, self.limit, self.tmpdir = work_mem, fan_in, limit, tmpdir
        self.held = 0
        self.stats = {"rows": 0, "runs": 0, "merge_passes": 0}

    def hold(self, n):
        self.held += n
        if self.held > self.work_mem:
            raise MemoryExceeded(f"holding {self.held} rows, work_mem is {self.work_mem}")

    def execute(self, rows):
        buffered = []
        for row in rows:
            self.stats["rows"] += 1
            self.hold(1)
            buffered.append(row)
        descending = bool(self.keys) and self.keys[0]["desc"]
        buffered.sort(key=row_key(self.keys), reverse=descending)
        if self.limit is not None:
            buffered = buffered[:self.limit]
        yield from buffered
        self.held = 0
