"""HA controller: lease grants and failover to the most up-to-date reachable node."""


class Controller:
    def __init__(self, lease):
        self.lease, self.term, self.granted = lease, 1, 0

    def renew(self, node, t):
        if node.term < self.term:
            return False
        self.granted = t
        node.lease_start = node.local(t)
        return True

    def elect(self, candidates):
        return max(sorted(candidates, key=lambda n: n.name), key=lambda n: len(n.log))

    def failover(self, t, candidates):
        if t - self.granted < self.lease or not candidates:
            return None
        winner = self.elect(candidates)
        self.term += 1
        for node in candidates:
            node.step_down(self.term)
        winner.role, winner.term = "primary", self.term
        self.renew(winner, t)
        return winner
