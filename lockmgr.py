"""Hierarchical lock table: modes, compatibility, FIFO queues and conversions.

Modes and the compatibility matrix follow the project README:

    held \\ requested | IS  IX  S  SIX  X
    IS               | y   y   y  y    n
    IX               | y   y   n  n    n
    S                | y   n   y  n    n
    SIX              | y   n   n  n    n
    X                | n   n   n  n    n

Every queue entry is (tx, mode, conversion).  New requests go to the tail;
conversions wait at the head, behind earlier conversions.  Grants always
advance from the head and stop at the first incompatible request, so an
exclusive request is never starved by later shared requests.
"""
from __future__ import annotations

# COMPAT[held] = set of request modes compatible with a holder in `held`.
COMPAT = {
    "IS": {"IS", "IX", "S", "SIX"},
    "IX": {"IS", "IX"},
    "S": {"IS", "S"},
    "SIX": {"IS"},
    "X": set(),
}
COVERS = {"X": {"IS", "IX", "S", "SIX", "X"}, "SIX": {"IS", "IX", "S", "SIX"},
          "S": {"IS", "S"}, "IX": {"IS", "IX"}, "IS": {"IS"}}


def join(held, wanted):
    """Mode resulting from a conversion request by an existing holder."""
    if wanted in COVERS[held]:
        return held
    if held in COVERS[wanted]:
        return wanted
    return "SIX" if {held, wanted} == {"S", "IX"} else "X"


def compatible(held_mode, requested_mode):
    return requested_mode in COMPAT[held_mode]


class Resource:
    def __init__(self):
        # tx -> granted mode
        self.granted = {}
        # list of (tx, mode, conversion: bool), FIFO
        self.queue = []

    def fits(self, mode, ignore=None):
        """True when `mode` is compatible with every granted lock (except `ignore`)."""
        return all(compatible(m, mode) for t, m in self.granted.items() if t != ignore)

    def queued_tx(self):
        return {tx for tx, _, _ in self.queue}


class LockTable:
    def __init__(self):
        self.resources = {}
        # tx -> set of resources where it currently holds a lock
        self.held = {}

    def _resource(self, res):
        return self.resources.setdefault(res, Resource())

    def request(self, tx, res, mode):
        """Request a lock.

        Returns the granted mode string when the request is granted at once
        (a covered conversion re-grants the held mode), or None when it had
        to join the waiting queue.
        """
        r = self._resource(res)
        conversion = tx in r.granted
        if conversion:
            mode = join(r.granted[tx], mode)
            if mode == r.granted[tx]:
                return mode
            # A conversion only contends with the other holders.
            if r.fits(mode, ignore=tx):
                self._grant(r, tx, res, mode)
                return mode
            # Wait behind earlier conversions, ahead of ordinary requests.
            idx = sum(1 for entry in r.queue if entry[2])
            r.queue.insert(idx, (tx, mode, True))
            return None
        # New request: nobody may be queued ahead and all holders must fit.
        if not r.queue and r.fits(mode):
            self._grant(r, tx, res, mode)
            return mode
        r.queue.append((tx, mode, False))
        return None

    def _grant(self, r, tx, res, mode):
        r.granted[tx] = mode
        self.held.setdefault(tx, set()).add(res)

    def is_waiting(self, tx):
        return any(tx in r.queued_tx() for r in self.resources.values())

    def queued_mode(self, tx):
        """Effective mode tx is currently waiting for, or None."""
        for r in self.resources.values():
            for t, mode, _ in r.queue:
                if t == tx:
                    return mode
        return None

    def wait_edges(self, waiter):
        """Wait-for edges waiter -> blocker for the transaction's queued request.

        Edges point at every holder of an incompatible lock and, for ordinary
        requests, at every incompatible queue entry ahead.  Conversions only
        wait for holders (README: "conversions only wait for holders").
        """
        edges = set()
        for res, r in self.resources.items():
            pos = next((i for i, (t, _, _) in enumerate(r.queue) if t == waiter), None)
            if pos is None:
                continue
            mode = r.queue[pos][1]
            is_conversion = r.queue[pos][2]
            for holder, held_mode in r.granted.items():
                if holder != waiter and not compatible(held_mode, mode):
                    edges.add(holder)
            if not is_conversion:
                for t, queued_mode, _ in r.queue[:pos]:
                    if not compatible(queued_mode, mode):
                        edges.add(t)
        return edges

    def release_all(self, tx):
        """Release every lock and drop every queued request of `tx`.

        Returns (woken, touched) where woken is a list of
        (tx, res, mode) granted while draining the queues, in grant order, and
        touched lists the resources whose queue changed (for graph refresh).
        """
        woken = []
        touched = []
        for res, r in self.resources.items():
            filtered = [entry for entry in r.queue if entry[0] != tx]
            if len(filtered) != len(r.queue):
                r.queue = filtered
                touched.append(res)
            if r.granted.pop(tx, None) is None:
                continue
            touched.append(res)
            # Drain from the head until the first request still incompatible.
            while r.queue:
                t, mode, _ = r.queue[0]
                if not r.fits(mode, ignore=t):
                    break
                r.queue.pop(0)
                self._grant(r, t, res, mode)
                woken.append((t, res, mode))
        self.held.pop(tx, None)
        return woken, touched
