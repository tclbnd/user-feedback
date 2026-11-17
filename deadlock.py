"""Lock wait timeouts."""

LOCK_TIMEOUT = 3


class Watchdog:
    """Aborts a waiter when the lock table has made no progress for a while."""

    def __init__(self, timeout=LOCK_TIMEOUT):
        self.timeout = timeout
        self.clock = 0
        self.last_grant = 0
        self.waiters = []

    def tick(self):
        self.clock += 1

    def granted(self):
        self.last_grant = self.clock

    def waiting(self, tx):
        self.waiters.append(tx)

    def resumed(self, tx):
        if tx in self.waiters:
            self.waiters.remove(tx)

    def expired(self):
        if self.waiters and self.clock - self.last_grant >= self.timeout:
            return self.waiters[-1]
        return None
