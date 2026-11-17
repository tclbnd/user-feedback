"""Lock table: record, gap, next-key and insert intention locks on index entries."""

RECORD, GAP, NEXT_KEY, INSERT = "record", "gap", "next_key", "insert_intention"


def covers_record(kind):
    return kind in (RECORD, NEXT_KEY)


def covers_gap(kind):
    return kind in (GAP, NEXT_KEY)


def conflicts(kind, mode, held_kind, held_mode):
    # Record locks use the ordinary shared/exclusive matrix.
    if covers_record(kind) and covers_record(held_kind):
        return mode == "X" or held_mode == "X"

    # An insert intention only waits for a lock that protects the gap.
    # Ordinary gap locks, including next-key gap parts, are mutually compatible.
    return kind == INSERT and covers_gap(held_kind)


class LockTable:
    def __init__(self):
        self.locks = set()

    def blockers(self, tx, requests):
        return {h_tx for target, kind, mode in requests
                for h_tx, h_target, h_kind, h_mode in self.locks
                if h_target == target and h_tx != tx and conflicts(kind, mode, h_kind, h_mode)}

    def grant(self, tx, requests):
        self.locks |= {(tx, t, k, m) for t, k, m in requests if k != INSERT}

    def release(self, tx):
        self.locks = {lock for lock in self.locks if lock[0] != tx}

    def release_requests(self, tx, requests):
        requested = {(target, kind, mode) for target, kind, mode in requests}
        self.locks = {lock for lock in self.locks
                      if not (lock[0] == tx
                              and (lock[1], lock[2], lock[3]) in requested)}

    def move_gap_locks(self, target, successor):
        """Move only the gap parts of locks attached to an existing entry."""
        moved = set()
        for tx, locked_target, kind, mode in self.locks:
            if locked_target == target and covers_gap(kind):
                if kind == NEXT_KEY:
                    kind = GAP
                moved.add((tx, successor, kind, mode))
        self.locks = {lock for lock in self.locks
                      if not (lock[1] == target and covers_gap(lock[2]))}
        self.locks |= moved

    def entry_removed(self, target, successor):
        """Move gap locks from a removed entry, then drop its record locks."""
        self.move_gap_locks(target, successor)
        self.locks = {lock for lock in self.locks if lock[1] != target}

    def entry_added(self, tx, target, successor):
        self.locks |= {(tx, target, GAP, m) for h_tx, t, k, m in self.locks
                       if h_tx == tx and t == successor and covers_gap(k)}
