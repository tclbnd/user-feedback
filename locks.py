"""Lock table: record, gap, next-key and insert intention locks on index entries."""

RECORD, GAP, NEXT_KEY, INSERT = "record", "gap", "next_key", "insert_intention"


def covers_record(kind):
    return kind in (RECORD, NEXT_KEY)


def covers_gap(kind):
    return kind in (GAP, NEXT_KEY)


def conflicts(kind, mode, held_kind, held_mode):
    if kind == INSERT:
        return covers_gap(held_kind)
    if mode == "S" and held_mode == "S":
        return False
    return (covers_record(kind) and covers_record(held_kind)) or (covers_gap(kind) and covers_gap(held_kind))


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

    def entry_removed(self, target, successor):
        self.locks = {lock for lock in self.locks if lock[1] != target}

    def entry_added(self, tx, target, successor):
        self.locks |= {(tx, target, GAP, m) for h_tx, t, k, m in self.locks
                       if h_tx == tx and t == successor and covers_gap(k)}
