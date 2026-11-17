"""Index with delete marks, locking reads and writes, and a scripted scheduler."""
from locks import GAP, INSERT, NEXT_KEY, RECORD, LockTable

SUPREMUM = 0


class Wait(Exception):
    pass


class Engine:
    def __init__(self, keys, unique=True):
        self.unique = unique
        self.entries = []          # [key, row id, deleting tx or None], sorted by (key, row id)
        self.next_row = 1
        self.locks = LockTable()
        self.undo = {}
        for key in sorted(keys):
            self._add(key)

    def _add(self, key):
        entry = [key, self.next_row, None]
        self.next_row += 1
        self.entries.append(entry)
        self.entries.sort(key=lambda e: (e[0], e[1]))
        return entry

    def _gap_after(self, key):
        return next((e[1] for e in self.entries if e[0] > key), SUPREMUM)

    def _successor(self, entry):
        i = self.entries.index(entry) + 1
        return self.entries[i][1] if i < len(self.entries) else SUPREMUM

    def _lock(self, tx, requests):
        self.undo.setdefault(tx, [])
        if self.locks.blockers(tx, requests):
            raise Wait()
        self.locks.grant(tx, requests)

    def _point(self, key, mode):
        hits = [e for e in self.entries if e[0] == key]
        if self.unique and hits:
            return hits, [(hits[0][1], RECORD, mode)]
        return hits, [(e[1], NEXT_KEY, mode) for e in hits] + [(self._gap_after(key), GAP, mode)]

    def range(self, tx, low, high, mode):
        scanned = [e for e in self.entries if (low is None or e[0] >= low) and (high is None or e[0] <= high)]
        self._lock(tx, [(e[1], NEXT_KEY, mode) for e in scanned])
        return {"rows": [e[0] for e in scanned if e[2] is None]}

    def get(self, tx, key, mode):
        hits = [e for e in self.entries if e[0] == key]
        self._lock(tx, [(e[1], NEXT_KEY, mode) for e in hits] + [(self._gap_after(key), GAP, mode)])
        return {"rows": [e[0] for e in hits if e[2] is None]}

    def delete(self, tx, key):
        hits, requests = self._point(key, "X")
        self._lock(tx, requests)
        live = [e for e in hits if e[2] is None]
        for e in live:
            e[2] = tx
            self.undo[tx].append(("delete", e))
        return {"count": len(live)}

    def insert(self, tx, key):
        if self.unique:
            for e in [e for e in self.entries if e[0] == key and e[2] is None]:
                self._lock(tx, [(e[1], RECORD, "S")])
                return {"error": f"duplicate key {key}"}
        successor = self._gap_after(key)
        self._lock(tx, [(successor, INSERT, "X")])
        entry = self._add(key)
        self.locks.entry_added(tx, entry[1], successor)
        self.locks.grant(tx, [(entry[1], RECORD, "X")])
        self.undo[tx].append(("insert", entry))
        return {"count": 1}

    def _remove(self, entry):
        self.locks.entry_removed(entry[1], self._successor(entry))
        self.entries.remove(entry)

    def end(self, tx, commit):
        for action, e in reversed(self.undo.pop(tx, [])):
            if commit and action == "delete" and e[2] == tx and e in self.entries:
                self._remove(e)
            elif not commit:
                if action == "insert":
                    self._remove(e)
                else:
                    e[2] = None
        self.locks.release(tx)


def run_script(script):
    engine = Engine(script.get("rows", []), script.get("unique", True))
    results, waiting = [], []

    def attempt(res, step):
        mode = "X" if step.get("for_update") else "S"
        op = step["op"]
        if op == "range":
            out = engine.range(step["tx"], step.get("low"), step.get("high"), mode)
        elif op == "get":
            out = engine.get(step["tx"], step["key"], mode)
        else:
            out = getattr(engine, op)(step["tx"], step["key"])
        res.update(out, status="error" if "error" in out else "ok")

    for i, step in enumerate(script["steps"]):
        if any(w[1]["tx"] == step["tx"] for w in waiting):
            raise SystemExit(f"step {i}: transaction {step['tx']} is still waiting for a lock")
        res = {"step": i, "tx": step["tx"], "op": step["op"], "waited": False}
        results.append(res)
        if step["op"] in ("commit", "rollback"):
            engine.end(step["tx"], step["op"] == "commit")
            res["status"] = "ok"
            progress = True
            while progress:
                progress = False
                for w in list(waiting):
                    try:
                        attempt(*w)
                    except Wait:
                        continue
                    w[0]["resumed_at"] = i
                    waiting.remove(w)
                    progress = True
                    break
            continue
        try:
            attempt(res, step)
        except Wait:
            res.update(waited=True, status="waiting")
            waiting.append((res, step))
    return {"results": results, "table": [e[0] for e in engine.entries if e[2] is None]}
