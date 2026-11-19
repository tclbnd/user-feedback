"""Tick-based scheduler that drives sessions, timeouts and cancel requests."""
from .locks import LockManager
from .session import Session


def run(spec, max_ticks=500):
    db = {t: dict(rows) for t, rows in spec.get("data", {}).items()}
    locks = LockManager()
    sessions = {}
    for sid, cfg in spec["sessions"].items():
        sessions[sid] = Session(sid, db, locks, cfg["script"], cfg.get("statement_timeout"))
    cancels = {}
    for c in spec.get("cancel", []):
        cancels.setdefault(c["tick"], []).append(c["session"])

    tick = 0
    for tick in range(max_ticks):
        for sid in cancels.get(tick, []):
            sessions[sid].interrupt("canceled")
        for s in sessions.values():
            if s.busy() and s.timeout is not None and tick - s.started >= s.timeout:
                s.interrupt("timeout")
        for s in sessions.values():
            while not s.busy() and s.next_statement(tick):
                pass
            if s.busy():
                s.step(tick)
        for sid, res in locks.granted:
            sessions[sid].lock_granted(res)
        locks.granted.clear()
        if all(not s.busy() and not s.script for s in sessions.values()):
            break

    return {
        "data": db,
        "results": {sid: s.results for sid, s in sessions.items()},
        "states": {sid: s.state for sid, s in sessions.items()},
        "locks": locks.snapshot(),
        "waiting": locks.waiting(),
        "stuck": sorted(sid for sid, s in sessions.items() if s.busy() or s.script),
        "ticks": tick + 1,
    }
