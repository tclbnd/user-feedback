"""Version chains, visibility and vacuum."""
from txn import precedes


class Store:
    def __init__(self, tm):
        self.tm, self.chains = tm, {}

    def visible(self, v, xid, snap):
        # Creator side: own changes are visible to their writer (unless it has
        # already replaced them); anyone else's version must be committed and
        # no longer in progress for the snapshot.
        if v["xmin"] == xid:
            if v["xmax"] == xid:
                return False
        elif self.tm.status(v["xmin"]) != "committed" or snap.in_progress(v["xmin"]):
            return False
        if v["xmax"] == 0:
            return True
        if v["xmax"] == xid:
            return False
        # A replacement that aborted, is still running, or is in progress for
        # this snapshot does not hide the version.
        deleter = self.tm.status(v["xmax"])
        return deleter in ("aborted", "running") or snap.in_progress(v["xmax"])

    def read(self, key, xid, snap):
        for v in self.chains.get(key, []):
            if self.visible(v, xid, snap):
                return v["value"]
        return None

    def write(self, key, value, xid, snap):
        chain = self.chains.setdefault(key, [])
        for v in chain:
            if self.visible(v, xid, snap):
                v["xmax"] = xid
                break
        chain.insert(0, {"xmin": xid, "xmax": 0, "value": value})

    def vacuum(self):
        horizon, removed = self.tm.horizon(), 0
        for key, chain in self.chains.items():
            kept, head_kept = [], False
            for v in chain:
                # Versions written by aborted transactions can never be visible.
                aborted = self.tm.status(v["xmin"]) == "aborted"
                # Versions replaced by a transaction that committed before the
                # global horizon cannot be visible to any active snapshot.
                dead = (v["xmax"] != 0 and self.tm.status(v["xmax"]) == "committed"
                        and precedes(v["xmax"], horizon))
                if (aborted or dead) and head_kept:
                    removed += 1
                    continue
                kept.append(v)
                head_kept = True
            self.chains[key] = kept
        return removed
