"""Transaction ids, commit log and snapshots."""
XID_SPACE, FIRST_XID = 2 ** 32, 3


def precedes(a, b):
    return a < b


def oldest(xids):
    best = None
    for x in xids:
        if best is None or precedes(x, best):
            best = x
    return best


class Snapshot:
    def __init__(self, xmax, xip):
        self.xmax, self.xip = xmax, set(xip)
        self.xmin = oldest(self.xip) if self.xip else xmax

    def in_progress(self, xid):
        return not precedes(xid, self.xmax) or xid in self.xip


class TxnManager:
    def __init__(self):
        self.next, self.clog, self.active = FIRST_XID, {}, {}

    def set_next(self, xid):
        if self.active:
            raise ValueError("cannot move the xid counter while transactions run")
        self.next = xid

    def begin(self):
        xid = self.next
        self.next = xid + 1 if xid + 1 < XID_SPACE else FIRST_XID
        snap = Snapshot(self.next, self.active)
        self.active[xid] = snap
        self.clog[xid] = "running"
        return xid, snap

    def finish(self, xid, status):
        self.clog[xid] = status
        del self.active[xid]

    def status(self, xid):
        return self.clog.get(xid, "aborted")

    def horizon(self):
        return oldest(self.active) if self.active else self.next
