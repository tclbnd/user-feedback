"""Version chains, visibility and vacuum."""
from txn import precedes


class Store:
    def __init__(self, tm):
        self.tm, self.chains = tm, {}

    def visible(self, v, xid, snap):
        if v["xmin"] == xid:
            if v["xmax"] == xid:
                return False
        elif self.tm.status(v["xmin"]) != "committed" or snap.in_progress(v["xmin"]):
            return False
        if v["xmax"] == 0:
            return True
        if v["xmax"] == xid:
            return False
        return (self.tm.status(v["xmax"]) in ("running", "aborted")
                or snap.in_progress(v["xmax"]))

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
            def dead(v):
                if self.tm.status(v["xmin"]) == "aborted":
                    return True
                return (v["xmax"] != 0 and self.tm.status(v["xmax"]) == "committed"
                        and precedes(v["xmax"], horizon))
            kept = [v for v in chain if not dead(v)]
            removed += len(chain) - len(kept)
            if not kept and chain:
                kept = [chain[0]]  # always keep the newest remaining version
                removed -= 1
            self.chains[key] = kept
        return removed
