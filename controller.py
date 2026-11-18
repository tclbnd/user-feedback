"""HA controller: lease grants and failover to the most up-to-date reachable node."""


class Controller:
    def __init__(self, lease):
        self.lease, self.term, self.granted = lease, 1, 0

    def lease_expired(self, t):
        """The controller may only replace a primary once the lease it last
        renewed has timed out on the controller's own (reference) clock."""
        return t - self.granted >= self.lease

    def renew(self, node, t):
        """Renew `node`'s lease.  A node from an older term is fenced."""
        if node.term < self.term:
            return False
        self.term = max(self.term, node.term)
        self.granted = t
        node.lease_start = node.local(t)
        return True

    def elect(self, candidates):
        """Pick the most up-to-date reachable log.

        Ordering is by the term of the last entry, then by log length, and
        finally by name (smaller wins) so the choice is deterministic.  The
        last entry's term matters: a long log full of stale-term entries is
        *less* up to date than a shorter log that already carries a newer term.
        """
        return min(candidates, key=lambda n: (-n.last()[0], -n.last()[1], n.name))

    def failover(self, t, candidates):
        """Promote a new primary if the lease has expired and somebody is reachable."""
        candidates = [n for n in candidates if n.alive]
        if not self.lease_expired(t) or not candidates:
            return None
        winner = self.elect(candidates)
        # A fresh term fences every stale primary: all reachable nodes adopt it,
        # so any later push or renewal from the old term is refused by storage.
        self.term = max([self.term] + [n.term for n in candidates]) + 1
        for node in candidates:
            node.step_down(self.term)
        winner.role, winner.term = "primary", self.term
        self.renew(winner, t)
        return winner
