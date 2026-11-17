"""Read routing with session consistency.

Implements the routing rules from README.md:

* replicas are tried in round-robin order starting from a cursor;
* a replica is eligible once its applied LSN reaches the session's
  ``max(last_write, last_read)``;
* with no eligible replica the read either waits for one (up to
  ``max_wait``) or falls back to the primary immediately;
* a replica that rejects a read is removed from routing until
  ``removed_at + probe_interval`` and the read is re-routed at once.
"""
from cluster import Unavailable


class Router:
    def __init__(self, cluster, policy):
        self.cluster = cluster
        self.on_stale = policy.get("on_stale", "primary")
        self.max_wait = policy.get("max_wait", 0)
        self.probe_interval = policy.get("probe_interval", 100)
        self.cursor = 0
        # replica name -> time at which it rejected its latest read
        self.removed_at = {}

    # -- membership -------------------------------------------------------

    def available(self, replica, t):
        """A replica participates in routing unless it is in its probe
        window after a rejection (rule 4)."""
        removed_at = self.removed_at.get(replica.name)
        return removed_at is None or t >= removed_at + self.probe_interval

    def candidates(self, t, skip=()):
        return [(i, r) for i, r in enumerate(self.cluster.replicas)
                if r.name not in skip and self.available(r, t)]

    def ready_at(self, replica, required, t):
        """Earliest time >= t at which ``replica`` can serve a read that
        needs ``required``: the later of its replay time and the end of its
        probe window."""
        return max(replica.eligible_at(required), t)

    # -- routing ----------------------------------------------------------

    def pick(self, t, required, skip=()):
        """First eligible replica at or after the cursor (wrapping once)."""
        eligible = {i for i, r in self.candidates(t, skip)
                    if r.applied(t) >= required}
        n = len(self.cluster.replicas)
        for step in range(n):
            index = (self.cursor + step) % n
            if index in eligible:
                self.cursor = (index + 1) % n
                return self.cluster.replicas[index]
        return None

    def read(self, key, t, required):
        arrival = t
        deadline = t + self.max_wait if self.on_stale == "wait" else t
        failed = []
        while True:
            replica = self.pick(t, required, failed)
            if replica is None:
                if self.on_stale == "wait":
                    # Earliest ready time among replicas still in routing
                    # (a rejecting one this read stays in `failed`).
                    waits = [self.ready_at(r, required, t)
                             for _, r in self.candidates(t, failed)]
                    waits = [w for w in waits if w <= deadline]
                    if waits:
                        t = min(waits)
                        continue
                # Primary fallback: immediately for on_stale=primary, at the
                # deadline for a timed-out wait.
                t = deadline
                return "primary", t, self.cluster.read("primary", key, t), failed
            try:
                result = self.cluster.read(replica.name, key, t)
            except Unavailable:
                # Rule 4: remove the replica and route again at the same
                # time with the same deadline.
                failed.append(replica.name)
                self.removed_at[replica.name] = t
                continue
            # A healthy answer clears any earlier removal of this replica.
            self.removed_at.pop(replica.name, None)
            return replica.name, t, result, failed
