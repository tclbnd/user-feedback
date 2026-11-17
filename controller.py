"""HA controller: lease grants and failover to the most up-to-date reachable node."""


class Controller:
    def __init__(self, lease):
        self.lease, self.term, self.granted = lease, 1, 0

    def renew(self, node, t):
        """Extend the lease of `node`; a node behind the controller steps down."""
        if node.term < self.term:
            return False
        self.term = max(self.term, node.term)
        self.granted = t
        node.grant_lease(t)
        return True

    def elect(self, candidates):
        """The candidate whose log is furthest ahead: (term of last entry, length).

        Sorting by name before taking the max makes the tie-break explicit
        (smaller name wins) and, unlike the original `max(..., key=len)`, the
        comparison really uses the term of the last entry first.
        """
        return max(sorted(candidates, key=lambda n: n.name), key=lambda n: n.last())

    def failover(self, t, candidates):
        """Promote the reachable node with the most up-to-date log.

        Called once the previous lease has expired.  The term goes to one more
        than the highest term anyone in reach already knows (and more than the
        controller's own), and every candidate the controller reaches adopts it
        before the winner becomes primary, so a candidate that missed the news
        can never win a later election with the old term.  The winner is the
        maximum of (last entry term, log length) with ties to the smaller name,
        so the freshest log wins.
        """
        if t - self.granted < self.lease or not candidates:
            return None
        winner = self.elect(candidates)
        # Never reuse or go below a term a candidate already knows: the term
        # must increase monotonically across the whole cluster.
        new_term = max([self.term] + [n.term for n in candidates]) + 1
        for node in candidates:
            node.step_down(new_term)
        self.term = new_term
        winner.role, winner.term = "primary", new_term
        winner.grant_lease(t)
        self.granted = t
        return winner
