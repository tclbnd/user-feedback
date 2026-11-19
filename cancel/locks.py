"""Lock manager for table and row locks.

Resources are ("table", name) or ("row", table, key).  Requests that cannot
be granted wait in a FIFO queue per resource and are granted when the
conflicting holders release.
"""
from collections import defaultdict

COMPAT = {
    "IS": {"IS", "IX", "S"},
    "IX": {"IS", "IX"},
    "S": {"IS", "S"},
    "X": set(),
}


class LockManager:
    def __init__(self):
        self.holders = defaultdict(dict)   # resource -> {sid: mode}
        self.queue = defaultdict(list)     # resource -> [(sid, mode)]
        self.granted = []                  # (sid, resource) granted from the queue since last poll

    def _ok(self, res, sid, mode):
        return all(m in COMPAT[mode] for s, m in self.holders[res].items() if s != sid)

    def acquire(self, sid, res, mode):
        held = self.holders[res].get(sid)
        if held == mode or held == "X":
            return True
        if not self.queue[res] and self._ok(res, sid, mode):
            self.holders[res][sid] = mode
            return True
        self.queue[res].append((sid, mode))
        return False

    def cancel_wait(self, sid, res):
        self.queue[res] = [(s, m) for s, m in self.queue[res] if s != sid]

    def release(self, sid, kinds=("table", "row")):
        touched = []
        for res in list(self.holders):
            if res[0] in kinds and sid in self.holders[res]:
                del self.holders[res][sid]
                touched.append(res)
        for res in touched:
            self._wake(res)

    def _wake(self, res):
        while self.queue[res]:
            sid, mode = self.queue[res][0]
            if not self._ok(res, sid, mode):
                break
            self.queue[res].pop(0)
            self.holders[res][sid] = mode
            self.granted.append((sid, res))

    def held_by(self, sid):
        return sorted(res for res, h in self.holders.items() if sid in h)

    def snapshot(self):
        return {"|".join(map(str, res)): dict(h) for res, h in sorted(self.holders.items()) if h}

    def waiting(self):
        return {"|".join(map(str, res)): [s for s, _ in q] for res, q in sorted(self.queue.items()) if q}
