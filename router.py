"""Read routing with session consistency.

Guarantees per session:

* read-your-writes: a replica must have replayed the session's latest commit
  LSN (``last_write``) before it may serve the session;
* monotonic reads: a replica must be at least as fresh as the node that
  served the session's previous read (``last_read``).

A replica rejecting a read is taken out of rotation and probed again after
``probe_interval``; if it is still down it is removed anew, otherwise it
rejoins the round-robin cursor.
"""
from cluster import Unavailable


class Router:
    def __init__(self, cluster, policy):
        self.cluster = cluster
        self.on_stale = policy.get("on_stale", "primary")
        self.max_wait = policy.get("max_wait", 0)
        self.probe_interval = policy.get("probe_interval", 100)
        # Single round-robin cursor over the *configured* replica list.
        self.cursor = 0
        # replica name -> simulated time at which it rejected a read
        self.removed = {}

    # ------------------------------------------------------------------ #
    # Rotation state
    # ------------------------------------------------------------------ #
    def in_rotation(self, replica, t):
        """A removed replica is only considered again at/after probe time."""
        removed_at = self.removed.get(replica.name)
        return removed_at is None or t >= removed_at + self.probe_interval

    def ready_at(self, replica, required, t):
        """Earliest time >= t at which ``replica`` can serve ``required``."""
        ready = replica.eligible_at(required)
        removed_at = self.removed.get(replica.name)
        if removed_at is not None:
            # It cannot serve before the next probe, even if its LSN is ready.
            ready = max(ready, removed_at + self.probe_interval)
        return max(ready, t)

    def pick(self, t, required, failed):
        """First eligible replica at or after the cursor, wrapping once."""
        n = len(self.cluster.replicas)
        for step in range(n):
            index = (self.cursor + step) % n
            replica = self.cluster.replicas[index]
            if replica.name in failed or not self.in_rotation(replica, t):
                continue
            if replica.applied(t) >= required:
                self.cursor = (index + 1) % n
                return replica
        return None

    # ------------------------------------------------------------------ #
    # Read entry point
    # ------------------------------------------------------------------ #
    def read(self, key, t, required):
        deadline = t + self.max_wait if self.on_stale == "wait" else t
        failed = []  # replicas that rejected *this* read, in order
        while True:
            replica = self.pick(t, required, failed)
            if replica is not None:
                try:
                    result = self.cluster.read(replica.name, key, t)
                except Unavailable:
                    # Rule 4: reject -> remove now and re-route at the same
                    # time, keeping the original deadline.
                    self.removed[replica.name] = t
                    failed.append(replica.name)
                    continue
                # A successful probe puts the replica fully back in rotation.
                self.removed.pop(replica.name, None)
                return replica.name, t, result, failed

            # No eligible replica right now.
            if self.on_stale != "wait":
                return self._primary(key, t, failed)

            options = [
                self.ready_at(r, required, t)
                for r in self.cluster.replicas
                if r.name not in failed
            ]
            options = [ready for ready in options if ready <= deadline]
            if options:
                # Wait until the earliest eligibility instant, then re-pick
                # (round-robin among every replica eligible at that time).
                t = min(options)
                continue
            # Nothing catches up within max_wait: primary at the deadline.
            return self._primary(key, deadline, failed)

    def _primary(self, key, t, failed):
        return "primary", t, self.cluster.read("primary", key, t), failed
