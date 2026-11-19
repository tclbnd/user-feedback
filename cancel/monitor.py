"""pg_locks-style view for the ops console."""


def lock_report(snapshot, waiting):
    rows = []
    for res, holders in snapshot.items():
        for sid, mode in sorted(holders.items()):
            rows.append({"resource": res, "session": sid, "mode": mode, "granted": True})
    for res, sids in waiting.items():
        for sid in sids:
            rows.append({"resource": res, "session": sid, "mode": None, "granted": False})
    return rows


def blocked_sessions(waiting):
    return sorted({sid for sids in waiting.values() for sid in sids})
