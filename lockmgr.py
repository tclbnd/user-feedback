"""Hierarchical lock table: modes, compatibility, FIFO queues and conversions."""

COMPAT = {
    "IS": {"IS", "IX", "S", "SIX"},
    "IX": {"IS", "IX", "S"},
    "S": {"IS", "S"},
    "SIX": {"IS"},
    "X": set(),
}
COVERS = {"X": {"IS", "IX", "S", "SIX", "X"}, "SIX": {"IS", "IX", "S", "SIX"},
          "S": {"IS", "S"}, "IX": {"IS", "IX"}, "IS": {"IS"}}


def join(held, wanted):
    if wanted in COVERS[held]:
        return held
    if held in COVERS[wanted]:
        return wanted
    return "SIX" if {held, wanted} == {"S", "IX"} else "X"


class Resource:
    def __init__(self):
        self.granted = {}
        self.queue = []

    def fits(self, mode):
        return all(mode in COMPAT[m] for m in self.granted.values())


class LockTable:
    def __init__(self):
        self.resources = {}
        self.held = {}

    def request(self, tx, res, mode):
        """Grant at once (True) or queue the request (False)."""
        r = self.resources.setdefault(res, Resource())
        if tx in r.granted:
            mode = join(r.granted[tx], mode)
            if mode == r.granted[tx]:
                return True
        if r.fits(mode):
            self._grant(r, tx, res, mode)
            return True
        r.queue.append((tx, mode))
        return False

    def _grant(self, r, tx, res, mode):
        r.granted[tx] = mode
        self.held.setdefault(tx, set()).add(res)

    def release_all(self, tx):
        """Drop every lock and queued request of tx; return newly granted (tx, res, mode)."""
        woken = []
        for res, r in self.resources.items():
            r.queue = [q for q in r.queue if q[0] != tx]
            if r.granted.pop(tx, None) is None:
                continue
            for t, mode in list(r.queue):
                if r.fits(mode):
                    r.queue.remove((t, mode))
                    self._grant(r, t, res, mode)
                    woken.append((t, res, mode))
        self.held.pop(tx, None)
        return woken
